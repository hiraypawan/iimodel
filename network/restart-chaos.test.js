'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const fsp = require('node:fs/promises');
const net = require('node:net');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');
const { spawn } = require('node:child_process');
const {
  encodePublicKey,
  generateHostKeyPair,
  signHostPayload,
} = require('../protocol/crypto');
const { signManifest } = require('../protocol/model-manifest');

function freePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const { port } = server.address();
      server.close((error) => (error ? reject(error) : resolve(port)));
    });
  });
}

async function waitForHealth(base, child) {
  const deadline = Date.now() + 10_000;
  while (Date.now() < deadline) {
    if (child.exitCode !== null) throw new Error(`Coordinator exited with ${child.exitCode}.`);
    try {
      const response = await fetch(`${base}/health`);
      if (response.ok) return;
    } catch { /* Process is still starting. */ }
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  throw new Error('Coordinator did not become healthy.');
}

function startCoordinator(env) {
  return spawn(process.execPath, [path.join(__dirname, 'server.js')], {
    env: { ...process.env, ...env },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
}

function stopped(child, signal = 'SIGTERM') {
  return new Promise((resolve) => {
    if (!child || child.exitCode !== null || child.signalCode) return resolve();
    child.once('exit', resolve);
    child.kill(signal);
  });
}

async function json(url, options = {}) {
  const response = await fetch(url, options);
  const body = response.status === 204 ? null : await response.json();
  return { response, body };
}

test('coordinator crash/restart preserves encrypted jobs and active attempt receipts', async (t) => {
  const directory = await fsp.mkdtemp(path.join(os.tmpdir(), 'ii-restart-chaos-'));
  const port = await freePort();
  const base = `http://127.0.0.1:${port}`;
  const releaseKeys = crypto.generateKeyPairSync('ed25519');
  const environment = {
    HOST: '127.0.0.1',
    PORT: String(port),
    II_DB_PATH: path.join(directory, 'network.db'),
    II_DATA_ENCRYPTION_KEY: crypto.randomBytes(32).toString('base64'),
    II_ADMIN_API_TOKEN: 'chaos-admin-token-32-bytes-long',
    II_JOB_API_TOKEN: 'chaos-job-token-32-bytes-long',
    II_RELEASE_PUBLIC_KEY: encodePublicKey(releaseKeys.publicKey),
  };
  let coordinator = startCoordinator(environment);
  t.after(async () => {
    await stopped(coordinator);
    await fsp.rm(directory, { recursive: true, force: true });
  });
  await waitForHealth(base, coordinator);

  const manifest = signManifest({
    schema: 'ii.model-manifest.v1',
    model_id: 'ii/restart-worker',
    version: '1.0.0',
    role: 'worker',
    runtime: 'node-llama-cpp@3.5.0',
    license: { id: 'Apache-2.0', redistribution_confirmed: true },
    created_at: '2026-10-23T00:00:00.000Z',
    artifacts: [{
      id: 'worker', sha256: 'f'.repeat(64), size_bytes: 10 * 1024 * 1024,
      format: 'gguf', url: 'https://models.example/restart.gguf',
    }],
  }, releaseKeys.privateKey, releaseKeys.publicKey);
  await json(`${base}/admin/models`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${environment.II_ADMIN_API_TOKEN}` },
    body: JSON.stringify(manifest),
  });

  const generated = generateHostKeyPair();
  const host = {
    ...generated,
    privateKey: crypto.createPrivateKey(generated.privateKeyPem),
    lastSignedAt: 0,
  };
  const signed = (action, payload = {}, includeKey = false) => {
    host.lastSignedAt = Math.max(Date.now(), host.lastSignedAt + 1);
    return signHostPayload(host.privateKey, action, {
      ...payload,
      device_id: host.deviceId,
      ...(includeKey ? { public_key: host.publicKey } : {}),
    }, host.lastSignedAt);
  };
  const claim = {
    manifest_id: manifest.manifest_id,
    artifact_id: 'worker',
    sha256: 'f'.repeat(64),
    size_bytes: 10 * 1024 * 1024,
    format: 'gguf',
    inference_ready: true,
  };
  const registered = await json(`${base}/register`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(signed('host:register', { artifacts: [claim] }, true)),
  });
  assert.equal(registered.response.status, 201);
  const submitted = await json(`${base}/jobs`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${environment.II_JOB_API_TOKEN}` },
    body: JSON.stringify({
      manifest_id: manifest.manifest_id,
      prompt: 'Survive a coordinator process crash.',
      max_tokens: 16,
    }),
  });
  const leased = await json(`${base}/worker/lease`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(signed('worker:lease')),
  });
  assert.equal(leased.body.job.id, submitted.body.id);

  await stopped(coordinator, 'SIGKILL');
  coordinator = startCoordinator(environment);
  await waitForHealth(base, coordinator);

  const duringRestart = await json(`${base}/jobs/${submitted.body.id}`, {
    headers: { Authorization: `Bearer ${submitted.body.access_token}` },
  });
  assert.equal(duringRestart.body.job.status, 'leased');
  assert.equal(duringRestart.body.job.attempt_summary.active, 1);
  const completed = await json(`${base}/worker/jobs/${submitted.body.id}/complete`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(signed('worker:complete', {
      attempt_id: leased.body.job.attempt_id,
      lease_token: leased.body.job.lease_token,
      output: 'Durable result after restart.',
      metrics: { duration_ms: 9 },
    })),
  });
  assert.equal(completed.body.status, 'completed');
  const final = await json(`${base}/jobs/${submitted.body.id}`, {
    headers: { Authorization: `Bearer ${submitted.body.access_token}` },
  });
  assert.equal(final.body.job.result, 'Durable result after restart.');
  const databaseBytes = await fsp.readFile(environment.II_DB_PATH);
  assert.doesNotMatch(databaseBytes.toString('utf8'), /Survive a coordinator process crash/);
});
