'use strict';

const http = require('node:http');
const crypto = require('node:crypto');

const MAX_ACTIVATION_BYTES = 8 * 1024 * 1024;
const CLOCK_WINDOW_MS = 60_000;

function sha256(value) {
  return crypto.createHash('sha256').update(value).digest('hex');
}

function authMessage({ requestId, timestamp, nonce, layerStart, layerEnd, width, bodyHash }) {
  return `ii-activation-v1\n${requestId}\n${timestamp}\n${nonce}\n${layerStart}\n${layerEnd}\n${width}\n${bodyHash}`;
}

function signActivationHeaders(secret, metadata, body, now = Date.now(), nonce = crypto.randomBytes(16).toString('base64url')) {
  const fields = {
    ...metadata,
    timestamp: now,
    nonce,
    bodyHash: sha256(body),
  };
  return {
    'x-ii-request-id': fields.requestId,
    'x-ii-timestamp': String(now),
    'x-ii-nonce': nonce,
    'x-ii-layer-start': String(fields.layerStart),
    'x-ii-layer-end': String(fields.layerEnd),
    'x-ii-width': String(fields.width),
    'x-ii-input-sha256': fields.bodyHash,
    'x-ii-signature': crypto.createHmac('sha256', secret).update(authMessage(fields)).digest('base64'),
  };
}

function transform(values, layerStart, layerEnd) {
  let input = Float32Array.from(values);
  for (let layer = layerStart; layer < layerEnd; layer += 1) {
    const output = new Float32Array(input.length);
    const scale = 0.91 + ((layer % 7) * 0.011);
    const mixing = 0.031 + ((layer % 5) * 0.003);
    const bias = ((layer % 11) - 5) * 0.0017;
    for (let index = 0; index < input.length; index += 1) {
      const neighbour = input[(index + layer + 1) % input.length];
      output[index] = Math.fround(Math.tanh((input[index] * scale) + (neighbour * mixing) + bias));
    }
    input = output;
  }
  return input;
}

function floatsToBuffer(values) {
  const buffer = Buffer.allocUnsafe(values.length * 4);
  for (let index = 0; index < values.length; index += 1) buffer.writeFloatLE(values[index], index * 4);
  return buffer;
}

function bufferToFloats(buffer) {
  if (buffer.length % 4 !== 0) throw new Error('Activation payload length must be a multiple of four.');
  const output = new Float32Array(buffer.length / 4);
  for (let index = 0; index < output.length; index += 1) output[index] = buffer.readFloatLE(index * 4);
  return output;
}

function secureEqual(left, right) {
  const a = Buffer.from(String(left || ''));
  const b = Buffer.from(String(right || ''));
  return a.length === b.length && crypto.timingSafeEqual(a, b);
}

function createActivationWorker({ secret, delayMs = 0, maxActivationBytes = MAX_ACTIVATION_BYTES } = {}) {
  if (typeof secret !== 'string' || Buffer.byteLength(secret) < 24) {
    throw new Error('Activation worker secret must contain at least 24 bytes.');
  }
  const nonces = new Map();
  let requests = 0;

  return http.createServer(async (req, res) => {
    if (req.method === 'GET' && req.url === '/health') {
      const body = Buffer.from(JSON.stringify({
        ok: true,
        service: 'ii-activation-reference-worker',
        pid: process.pid,
        reference_model: 'deterministic-nonlinear-v1',
        production_model: false,
        requests,
      }));
      res.writeHead(200, { 'Content-Type': 'application/json', 'Content-Length': body.length });
      res.end(body);
      return;
    }
    if (req.method !== 'POST' || req.url !== '/v1/activation') {
      res.writeHead(404).end();
      return;
    }
    const chunks = [];
    let size = 0;
    try {
      for await (const chunk of req) {
        size += chunk.length;
        if (size > maxActivationBytes) throw Object.assign(new Error('Activation exceeds byte limit.'), { status: 413 });
        chunks.push(chunk);
      }
      const body = Buffer.concat(chunks);
      const requestId = String(req.headers['x-ii-request-id'] || '');
      const timestamp = Number(req.headers['x-ii-timestamp']);
      const nonce = String(req.headers['x-ii-nonce'] || '');
      const layerStart = Number(req.headers['x-ii-layer-start']);
      const layerEnd = Number(req.headers['x-ii-layer-end']);
      const width = Number(req.headers['x-ii-width']);
      const bodyHash = sha256(body);
      if (!requestId || !Number.isSafeInteger(timestamp) || Math.abs(Date.now() - timestamp) > CLOCK_WINDOW_MS
          || !/^[A-Za-z0-9_-]{20,80}$/.test(nonce)
          || !Number.isInteger(layerStart) || !Number.isInteger(layerEnd)
          || layerStart < 0 || layerEnd <= layerStart || layerEnd - layerStart > 256
          || !Number.isInteger(width) || width < 1 || width > maxActivationBytes / 4
          || body.length !== width * 4
          || req.headers['x-ii-input-sha256'] !== bodyHash) {
        res.writeHead(400).end();
        return;
      }
      const expected = crypto.createHmac('sha256', secret).update(authMessage({
        requestId, timestamp, nonce, layerStart, layerEnd, width, bodyHash,
      })).digest('base64');
      if (!secureEqual(req.headers['x-ii-signature'], expected)) {
        res.writeHead(401).end();
        return;
      }
      const nonceKey = `${requestId}:${nonce}`;
      if (nonces.has(nonceKey)) {
        res.writeHead(409).end();
        return;
      }
      nonces.set(nonceKey, timestamp);
      if (nonces.size > 10_000) {
        for (const [key, createdAt] of nonces) if (createdAt < Date.now() - CLOCK_WINDOW_MS) nonces.delete(key);
      }
      if (delayMs > 0) await new Promise((resolve) => setTimeout(resolve, delayMs));
      const started = performance.now();
      const output = floatsToBuffer(transform(bufferToFloats(body), layerStart, layerEnd));
      const duration = Math.round((performance.now() - started) * 1000) / 1000;
      requests += 1;
      res.writeHead(200, {
        'Content-Type': 'application/octet-stream',
        'Content-Length': output.length,
        'X-II-Output-SHA256': sha256(output),
        'X-II-Worker-Pid': String(process.pid),
        'X-II-Compute-Ms': String(duration),
        'X-II-Reference-Model': 'deterministic-nonlinear-v1',
        'Cache-Control': 'no-store',
      });
      res.end(output);
    } catch (error) {
      res.writeHead(error.status || 500).end();
    }
  });
}

async function main() {
  const secret = process.env.II_ACTIVATION_SECRET;
  const host = process.env.HOST || '0.0.0.0';
  const port = Number.parseInt(process.env.PORT || '0', 10);
  const delayMs = Number.parseInt(process.env.II_ACTIVATION_DELAY_MS || '0', 10);
  const server = createActivationWorker({ secret, delayMs });
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(port, host, resolve);
  });
  const address = server.address();
  const message = {
    type: 'ready',
    pid: process.pid,
    host,
    port: address.port,
    reference_model: 'deterministic-nonlinear-v1',
    production_model: false,
  };
  if (process.send) process.send(message);
  else console.log(JSON.stringify(message));
  const shutdown = () => server.close(() => process.exit(0));
  process.on('SIGINT', shutdown);
  process.on('SIGTERM', shutdown);
}

if (require.main === module) {
  main().catch((error) => {
    console.error(`[activation-worker] ${error.stack || error.message}`);
    process.exit(1);
  });
}

module.exports = {
  authMessage,
  bufferToFloats,
  createActivationWorker,
  floatsToBuffer,
  sha256,
  signActivationHeaders,
  transform,
};
