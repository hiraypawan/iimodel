'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fsp = require('node:fs/promises');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const { encodePublicKey, verifyHostPayload } = require('../protocol/crypto');
const { signManifest } = require('../protocol/model-manifest');
const { loadOrCreateHostIdentity } = require('./host-identity');
const { fetchSignedManifest, selectArtifact } = require('./model-config');
const { WorkerAgent } = require('./worker-agent');

function listen(server) {
  return new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      server.off('error', reject);
      resolve(server.address());
    });
  });
}


test('persists a cryptographic host identity without putting the private key in metadata', async (t) => {
  const directory = await fsp.mkdtemp(path.join(os.tmpdir(), 'ii-identity-test-'));
  t.after(() => fsp.rm(directory, { recursive: true, force: true }));

  const first = await loadOrCreateHostIdentity(directory);
  const second = await loadOrCreateHostIdentity(directory);
  assert.equal(first.deviceId, second.deviceId);
  assert.equal(first.publicKey, second.publicKey);
  assert.match(first.privateKeyPem, /BEGIN PRIVATE KEY/);

  const metadata = await fsp.readFile(path.join(directory, 'device.json'), 'utf8');
  assert.doesNotMatch(metadata, /PRIVATE KEY/);
  assert.equal(JSON.parse(metadata).deviceId, first.deviceId);
  const keyMode = (await fsp.stat(path.join(directory, 'device-key.pem'))).mode & 0o777;
  if (process.platform !== 'win32') assert.equal(keyMode, 0o600);
});


test('verifies signed manifests and executes a leased worker job through the pure worker agent', async (t) => {
  const directory = await fsp.mkdtemp(path.join(os.tmpdir(), 'ii-worker-test-'));
  const identity = await loadOrCreateHostIdentity(directory);
  const publisher = crypto.generateKeyPairSync('ed25519');
  const trustedPublisher = encodePublicKey(publisher.publicKey);
  const manifest = signManifest({
    schema: 'ii.model-manifest.v1',
    model_id: 'ii/client-test',
    version: '1.0.0',
    role: 'worker',
    runtime: 'node-llama-cpp@3.5.0',
    license: { id: 'Apache-2.0', redistribution_confirmed: true },
    created_at: '2026-10-22T00:00:00.000Z',
    artifacts: [{
      id: 'worker',
      sha256: 'd'.repeat(64),
      size_bytes: 2048,
      format: 'gguf',
      url: 'https://models.example/worker.gguf',
    }],
  }, publisher.privateKey, publisher.publicKey);

  let completed = null;
  let leased = false;
  const server = http.createServer(async (req, res) => {
    if (req.method === 'GET' && req.url === `/models/${manifest.manifest_id}`) {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true, manifest }));
      return;
    }
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    const body = JSON.parse(Buffer.concat(chunks).toString('utf8') || '{}');
    if (req.url === '/worker/lease') {
      assert.equal(verifyHostPayload(identity.publicKey, 'worker:lease', body), true);
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({
        ok: true,
        job: {
          id: 'job-test',
          prompt: 'Run the real worker.',
          max_tokens: 16,
          lease_token: 'lease-secret',
        },
      }));
      leased = true;
      return;
    }
    if (req.url === '/worker/jobs/job-test/complete') {
      assert.equal(verifyHostPayload(identity.publicKey, 'worker:complete', body), true);
      assert.equal(body.lease_token, 'lease-secret');
      completed = body.output;
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true, status: 'completed' }));
      return;
    }
    res.writeHead(404, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ error: 'not_found' }));
  });
  const address = await listen(server);
  t.after(async () => {
    await new Promise((resolve) => server.close(resolve));
    await fsp.rm(directory, { recursive: true, force: true });
  });

  const coordinatorUrl = `http://127.0.0.1:${address.port}`;
  const fetched = await fetchSignedManifest({
    coordinatorUrl,
    manifestId: manifest.manifest_id,
    trustedPublicKey: trustedPublisher,
  });
  assert.equal(selectArtifact(fetched, { artifactId: 'worker' }).sha256, 'd'.repeat(64));

  const agent = new WorkerAgent({
    coordinatorUrl,
    identity,
    inference: {
      loaded: true,
      infer: async (prompt, maxTokens) => `answer:${prompt}:${maxTokens}`,
    },
    pollIntervalMs: 60_000,
  });
  agent.start();
  const result = await agent.runOnce();
  agent.stop();
  assert.equal(leased, true);
  assert.equal(result.status, 'completed');
  assert.equal(completed, 'answer:Run the real worker.:16');
});
