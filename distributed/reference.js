'use strict';

const crypto = require('node:crypto');
const {
  bufferToFloats,
  floatsToBuffer,
  sha256,
  signActivationHeaders,
} = require('./activation-worker');

function validNodeUrl(value) {
  try {
    const parsed = new URL(value);
    const local = parsed.protocol === 'http:' && ['localhost', '127.0.0.1', '[::1]'].includes(parsed.hostname);
    if (parsed.protocol !== 'https:' && !local) return null;
    if (parsed.username || parsed.password || parsed.search || parsed.hash) return null;
    return parsed.toString().replace(/\/$/, '');
  } catch {
    return null;
  }
}

class ActivationTransportError extends Error {
  constructor(message, details) {
    super(message);
    this.name = 'ActivationTransportError';
    this.details = details;
  }
}

class DistributedActivationReference {
  constructor({ nodes, secret, timeoutMs = 2_000, maxActivationBytes = 8 * 1024 * 1024 }) {
    if (!Array.isArray(nodes) || nodes.length < 2) throw new Error('At least two activation worker nodes are required.');
    if (typeof secret !== 'string' || Buffer.byteLength(secret) < 24) throw new Error('Activation secret must contain at least 24 bytes.');
    this.nodes = nodes.map((node, index) => {
      const url = validNodeUrl(node.url);
      if (!url || !Number.isInteger(node.layerStart) || !Number.isInteger(node.layerEnd)
          || node.layerEnd <= node.layerStart) throw new Error(`Invalid node partition at index ${index}.`);
      return { ...node, url };
    });
    for (let index = 1; index < this.nodes.length; index += 1) {
      if (this.nodes[index - 1].layerEnd !== this.nodes[index].layerStart) {
        throw new Error('Activation layer partitions must be contiguous and ordered.');
      }
    }
    this.secret = secret;
    this.timeoutMs = timeoutMs;
    this.maxActivationBytes = maxActivationBytes;
  }

  async execute(values, { requestId = `activation_${crypto.randomUUID()}` } = {}) {
    let activation = values instanceof Float32Array ? Float32Array.from(values) : Float32Array.from(values || []);
    if (!activation.length || activation.byteLength > this.maxActivationBytes) {
      throw new Error('Activation vector is empty or exceeds the byte limit.');
    }
    const stages = [];
    const startedAt = performance.now();
    for (let index = 0; index < this.nodes.length; index += 1) {
      const node = this.nodes[index];
      const input = floatsToBuffer(activation);
      const metadata = {
        requestId: `${requestId}:${index}`,
        layerStart: node.layerStart,
        layerEnd: node.layerEnd,
        width: activation.length,
      };
      const headers = signActivationHeaders(this.secret, metadata, input);
      const stageStarted = performance.now();
      let response;
      try {
        response = await fetch(`${node.url}/v1/activation`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/octet-stream', ...headers },
          body: input,
          signal: AbortSignal.timeout(this.timeoutMs),
        });
      } catch (error) {
        throw new ActivationTransportError(
          `Activation node ${index + 1} transport failed: ${error.message}`,
          {
            node_index: index,
            node_url: node.url,
            layer_start: node.layerStart,
            layer_end: node.layerEnd,
            elapsed_ms: Math.round((performance.now() - stageStarted) * 1000) / 1000,
            timeout_ms: this.timeoutMs,
            cause: error.name,
          },
        );
      }
      if (!response.ok) {
        throw new ActivationTransportError(
          `Activation node ${index + 1} returned HTTP ${response.status}.`,
          {
            node_index: index,
            node_url: node.url,
            layer_start: node.layerStart,
            layer_end: node.layerEnd,
            status: response.status,
            elapsed_ms: Math.round((performance.now() - stageStarted) * 1000) / 1000,
          },
        );
      }
      const output = Buffer.from(await response.arrayBuffer());
      const outputHash = sha256(output);
      if (output.length !== input.length || response.headers.get('x-ii-output-sha256') !== outputHash) {
        throw new ActivationTransportError('Activation response failed length or SHA-256 verification.', {
          node_index: index,
          node_url: node.url,
          expected_bytes: input.length,
          actual_bytes: output.length,
        });
      }
      const totalMs = Math.round((performance.now() - stageStarted) * 1000) / 1000;
      const computeMs = Number(response.headers.get('x-ii-compute-ms'));
      stages.push({
        node_index: index,
        node_url: node.url,
        worker_pid: Number(response.headers.get('x-ii-worker-pid')),
        layer_start: node.layerStart,
        layer_end: node.layerEnd,
        bytes_sent: input.length,
        bytes_received: output.length,
        input_sha256: sha256(input),
        output_sha256: outputHash,
        total_ms: totalMs,
        reported_compute_ms: Number.isFinite(computeMs) ? computeMs : null,
        measured_transport_overhead_ms: Number.isFinite(computeMs)
          ? Math.max(0, Math.round((totalMs - computeMs) * 1000) / 1000)
          : null,
      });
      activation = bufferToFloats(output);
    }
    return {
      schema: 'ii.activation-reference-result.v1',
      request_id: requestId,
      reference_model: 'deterministic-nonlinear-v1',
      production_model: false,
      width: activation.length,
      output: activation,
      output_sha256: sha256(floatsToBuffer(activation)),
      total_ms: Math.round((performance.now() - startedAt) * 1000) / 1000,
      total_network_bytes: stages.reduce((total, stage) => total + stage.bytes_sent + stage.bytes_received, 0),
      stages,
    };
  }

  async benchmark(values, { warmups = 1, runs = 5 } = {}) {
    if (!Number.isInteger(warmups) || warmups < 0 || warmups > 20
        || !Number.isInteger(runs) || runs < 1 || runs > 100) {
      throw new Error('Benchmark warmup/run counts are outside bounds.');
    }
    for (let index = 0; index < warmups; index += 1) await this.execute(values);
    const samples = [];
    let last;
    for (let index = 0; index < runs; index += 1) {
      last = await this.execute(values);
      samples.push(last.total_ms);
    }
    const ordered = [...samples].sort((left, right) => left - right);
    const percentile = (value) => ordered[Math.min(
      ordered.length - 1,
      Math.max(0, Math.ceil((value / 100) * ordered.length) - 1),
    )];
    return {
      schema: 'ii.activation-reference-benchmark.v1',
      reference_model: 'deterministic-nonlinear-v1',
      production_model: false,
      nodes: this.nodes.length,
      layers: this.nodes[this.nodes.length - 1].layerEnd - this.nodes[0].layerStart,
      width: values.length,
      warmups,
      runs,
      latency_ms: {
        samples,
        min: ordered[0],
        p50: percentile(50),
        p95: percentile(95),
        max: ordered[ordered.length - 1],
      },
      measured_network_bytes_per_run: last.total_network_bytes,
      last_stages: last.stages,
      gate: 'This measures the local reference transport only; it is not physical-LAN Qwen evidence.',
    };
  }
}

module.exports = {
  ActivationTransportError,
  DistributedActivationReference,
  validNodeUrl,
};
