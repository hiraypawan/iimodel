'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const crypto = require('node:crypto');
const { fork } = require('node:child_process');
const {
  floatsToBuffer,
  signActivationHeaders,
  transform,
} = require('./activation-worker');
const {
  ActivationTransportError,
  DistributedActivationReference,
} = require('./reference');

function startWorker(secret, delayMs = 0) {
  const child = fork(path.join(__dirname, 'activation-worker.js'), [], {
    env: {
      ...process.env,
      HOST: '127.0.0.1',
      PORT: '0',
      II_ACTIVATION_SECRET: secret,
      II_ACTIVATION_DELAY_MS: String(delayMs),
    },
    stdio: ['ignore', 'pipe', 'pipe', 'ipc'],
  });
  const stderr = [];
  child.stderr.on('data', (chunk) => stderr.push(chunk.toString()));
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => reject(new Error(`Worker startup timed out: ${stderr.join('')}`)), 5_000);
    child.once('error', reject);
    child.on('message', (message) => {
      if (message?.type === 'ready') {
        clearTimeout(timeout);
        resolve({ child, ready: message, url: `http://127.0.0.1:${message.port}` });
      }
    });
  });
}

function stopWorker(worker) {
  return new Promise((resolve) => {
    if (!worker || worker.child.exitCode !== null) return resolve();
    worker.child.once('exit', resolve);
    worker.child.kill('SIGTERM');
    setTimeout(() => worker.child.kill('SIGKILL'), 2_000).unref();
  });
}

test('two real worker processes transport activations and expose bounded failure measurements', async (t) => {
  const secret = crypto.randomBytes(32).toString('base64url');
  const workers = [await startWorker(secret, 3), await startWorker(secret, 5)];
  t.after(async () => Promise.all(workers.map(stopWorker)));

  const reference = new DistributedActivationReference({
    nodes: [
      { url: workers[0].url, layerStart: 0, layerEnd: 4 },
      { url: workers[1].url, layerStart: 4, layerEnd: 9 },
    ],
    secret,
    timeoutMs: 800,
  });
  const input = new Float32Array(2048);
  for (let index = 0; index < input.length; index += 1) input[index] = Math.sin(index * 0.03) * 0.4;

  const result = await reference.execute(input, { requestId: 'two-process-proof' });
  assert.equal(result.production_model, false);
  assert.equal(result.reference_model, 'deterministic-nonlinear-v1');
  assert.deepEqual(result.stages.map((stage) => stage.worker_pid), workers.map((worker) => worker.ready.pid));
  assert.ok(result.total_network_bytes >= input.byteLength * 4);
  assert.equal(result.stages[0].output_sha256, result.stages[1].input_sha256);
  const local = transform(transform(input, 0, 4), 4, 9);
  assert.deepEqual([...result.output], [...local]);

  const benchmark = await reference.benchmark(input, { warmups: 1, runs: 4 });
  assert.equal(benchmark.latency_ms.samples.length, 4);
  assert.ok(benchmark.latency_ms.p95 >= benchmark.latency_ms.p50);
  assert.ok(benchmark.measured_network_bytes_per_run > 0);
  assert.match(benchmark.gate, /not physical-LAN Qwen evidence/);

  const body = floatsToBuffer(input);
  const metadata = { requestId: 'tamper-check', layerStart: 0, layerEnd: 1, width: input.length };
  const headers = signActivationHeaders('wrong-secret-that-is-definitely-long-enough', metadata, body);
  const unauthorized = await fetch(`${workers[0].url}/v1/activation`, {
    method: 'POST', headers, body,
  });
  assert.equal(unauthorized.status, 401);

  await stopWorker(workers[1]);
  const failedAt = performance.now();
  await assert.rejects(reference.execute(input), (error) => {
    assert.ok(error instanceof ActivationTransportError);
    assert.equal(error.details.node_index, 1);
    assert.ok(error.details.elapsed_ms <= 800);
    return true;
  });
  assert.ok(performance.now() - failedAt < 1_200, 'transport failure must surface within the configured timeout');
});
