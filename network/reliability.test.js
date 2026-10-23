'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const fsp = require('node:fs/promises');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');
const {
  encodePublicKey,
  generateHostKeyPair,
  signHostPayload,
} = require('../protocol/crypto');
const { signManifest } = require('../protocol/model-manifest');

const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'ii-reliability-test-'));
const releaseKeys = crypto.generateKeyPairSync('ed25519');
process.env.II_DB_PATH = path.join(directory, 'network.db');
process.env.II_ADMIN_API_TOKEN = 'reliability-admin-token-32-bytes';
process.env.II_JOB_API_TOKEN = 'reliability-job-token-32-bytes';
process.env.II_DATA_ENCRYPTION_KEY = crypto.randomBytes(32).toString('base64');
process.env.II_RELEASE_PUBLIC_KEY = encodePublicKey(releaseKeys.publicKey);
const { app, db } = require('./server');

function listen(server) {
  return new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => resolve(server.address()));
  });
}

async function request(url, options = {}) {
  const response = await fetch(url, options);
  const body = response.status === 204 ? null : await response.json();
  return { response, body };
}

function host(region) {
  const generated = generateHostKeyPair();
  return {
    ...generated,
    privateKey: crypto.createPrivateKey(generated.privateKeyPem),
    lastSignedAt: 0,
    region,
  };
}

function signed(item, action, payload = {}, includePublicKey = false) {
  item.lastSignedAt = Math.max(Date.now(), item.lastSignedAt + 1);
  return signHostPayload(item.privateKey, action, {
    ...payload,
    device_id: item.deviceId,
    ...(includePublicKey ? { public_key: item.publicKey } : {}),
  }, item.lastSignedAt);
}

function headers(token) {
  return {
    'Content-Type': 'application/json',
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
}

function capabilities(item, overrides = {}) {
  return {
    region: item.region,
    architecture: 'test-x64',
    cpu_cores: 8,
    memory_mb: 16_384,
    gpu_backend: 'test',
    vram_mb: 8_192,
    max_concurrency: 1,
    queue_depth: 0,
    supported_runtimes: ['node-llama-cpp@3.5.0'],
    ...overrides,
  };
}

test('load/region scheduling, bounded hedging, challenge scores, quarantine, replicas, and churn', async (t) => {
  const server = http.createServer(app);
  const address = await listen(server);
  const base = `http://127.0.0.1:${address.port}`;
  t.after(async () => {
    await new Promise((resolve) => server.close(resolve));
    db.close();
    await fsp.rm(directory, { recursive: true, force: true });
  });

  const manifest = signManifest({
    schema: 'ii.model-manifest.v1',
    model_id: 'ii/reliability-worker',
    version: '1.0.0',
    role: 'worker',
    runtime: 'node-llama-cpp@3.5.0',
    license: { id: 'Apache-2.0', redistribution_confirmed: true },
    created_at: '2026-10-23T00:00:00.000Z',
    artifacts: [{
      id: 'worker-q4',
      sha256: 'c'.repeat(64),
      size_bytes: 10 * 1024 * 1024,
      format: 'gguf',
      url: 'https://models.example/reliability.gguf',
    }],
  }, releaseKeys.privateKey, releaseKeys.publicKey);
  const published = await request(`${base}/admin/models`, {
    method: 'POST',
    headers: headers(process.env.II_ADMIN_API_TOKEN),
    body: JSON.stringify(manifest),
  });
  assert.equal(published.response.status, 201);

  const workers = [host('IN-WEST'), host('US-EAST'), host('IN-SOUTH'), host('DE-CENTRAL')];
  const claim = {
    manifest_id: manifest.manifest_id,
    artifact_id: 'worker-q4',
    sha256: 'c'.repeat(64),
    size_bytes: 10 * 1024 * 1024,
    format: 'gguf',
    inference_ready: true,
  };
  for (const [index, worker] of workers.entries()) {
    const registered = await request(`${base}/register`, {
      method: 'POST',
      headers: headers(),
      body: JSON.stringify(signed(worker, 'host:register', {
        deviceName: `Worker ${index + 1}`,
        country: worker.region.slice(0, 2),
        artifacts: [claim],
        capabilities: capabilities(worker),
      }, true)),
    });
    assert.equal(registered.response.status, 201);
  }

  async function submit(overrides = {}) {
    return request(`${base}/jobs`, {
      method: 'POST',
      headers: headers(process.env.II_JOB_API_TOKEN),
      body: JSON.stringify({
        manifest_id: manifest.manifest_id,
        prompt: 'A deterministic reliability exercise.',
        max_tokens: 16,
        hedge_after_ms: 250,
        ...overrides,
      }),
    });
  }

  async function lease(worker, capabilityOverrides = {}) {
    return request(`${base}/worker/lease`, {
      method: 'POST',
      headers: headers(),
      body: JSON.stringify(signed(worker, 'worker:lease', {
        capabilities: capabilities(worker, capabilityOverrides),
      })),
    });
  }

  async function finish(worker, job, output) {
    return request(`${base}/worker/jobs/${job.id}/complete`, {
      method: 'POST',
      headers: headers(),
      body: JSON.stringify(signed(worker, 'worker:complete', {
        attempt_id: job.attempt_id,
        lease_token: job.lease_token,
        output,
        metrics: { duration_ms: 25, ignored_private_field: 'not persisted' },
      })),
    });
  }

  const regional = await submit({ desired_region: 'IN-WEST' });
  assert.equal(regional.response.status, 202);
  const nonPreferred = await lease(workers[1]);
  assert.equal(nonPreferred.response.status, 204, 'a live preferred-region host gets first refusal');
  const primary = await lease(workers[0]);
  assert.equal(primary.body.job.id, regional.body.id);
  db.prepare('UPDATE job_attempts SET leased_at = ? WHERE id = ?')
    .run(Date.now() - 1_000, primary.body.job.attempt_id);
  const hedge = await lease(workers[1]);
  assert.equal(hedge.body.job.id, regional.body.id);
  assert.equal(hedge.body.job.is_hedge, true);
  assert.equal(
    db.prepare(`SELECT COUNT(*) AS count FROM job_attempts WHERE job_id = ? AND status = 'active'`).get(regional.body.id).count,
    2,
    'hedging is capped at two active attempts',
  );
  const noThirdHedge = await lease(workers[2]);
  assert.equal(noThirdHedge.response.status, 204);
  const winner = await finish(workers[0], primary.body.job, 'same result');
  assert.equal(winner.body.status, 'completed');
  assert.deepEqual(winner.body.cancelled_attempt_ids, [hedge.body.job.attempt_id]);
  const cancelled = await request(`${base}/worker/attempts/${hedge.body.job.attempt_id}/status`, {
    method: 'POST',
    headers: headers(),
    body: JSON.stringify(signed(workers[1], 'worker:attempt-status', {
      lease_token: hedge.body.job.lease_token,
    })),
  });
  assert.equal(cancelled.body.cancel_requested, true);

  const duplicate = await submit({ verification: 'duplicate' });
  const duplicateA = await lease(workers[0]);
  db.prepare('UPDATE job_attempts SET leased_at = ? WHERE id = ?')
    .run(Date.now() - 1_000, duplicateA.body.job.attempt_id);
  const duplicateB = await lease(workers[1]);
  const firstResult = await finish(workers[0], duplicateA.body.job, 'answer A');
  assert.equal(firstResult.body.status, 'verifying');
  const conflict = await finish(workers[1], duplicateB.body.job, 'answer B');
  assert.equal(conflict.body.status, 'failed');
  assert.equal(conflict.body.conflict, true);
  const quarantined = db.prepare(`
    SELECT device_id, quarantine_until FROM worker_manifest_scores
    WHERE manifest_id = ? AND device_id IN (?, ?)
  `).all(manifest.manifest_id, workers[0].deviceId, workers[1].deviceId);
  assert.equal(quarantined.length, 2);
  assert.ok(quarantined.every((row) => row.quarantine_until > Date.now()));

  const challenge = await request(`${base}/admin/challenges`, {
    method: 'POST',
    headers: headers(process.env.II_ADMIN_API_TOKEN),
    body: JSON.stringify({
      manifest_id: manifest.manifest_id,
      prompt: 'Return the fixed test phrase.',
      expected_output: 'fixed phrase',
      max_tokens: 8,
    }),
  });
  assert.equal(challenge.response.status, 201);
  const challengeLease = await lease(workers[2]);
  assert.equal(challengeLease.body.job.kind, 'challenge');
  assert.equal(challengeLease.body.job.temperature, 0);
  const challengeResult = await finish(workers[2], challengeLease.body.job, ' fixed   phrase ');
  assert.equal(challengeResult.body.challenge, 'passed');
  assert.equal(
    db.prepare(`
      SELECT challenge_passes FROM worker_manifest_scores
      WHERE device_id = ? AND manifest_id = ?
    `).get(workers[2].deviceId, manifest.manifest_id).challenge_passes,
    1,
  );

  const queued = await submit();
  const loadedWorker = await lease(workers[3], { queue_depth: 1 });
  assert.equal(loadedWorker.response.status, 204, 'reported local load gates new leases');
  const availableWorker = await lease(workers[2]);
  assert.equal(availableWorker.body.job.id, queued.body.id);
  db.prepare('UPDATE job_attempts SET lease_expires_at = ? WHERE id = ?')
    .run(Date.now() - 1, availableWorker.body.job.attempt_id);
  const churnRecovery = await lease(workers[3], { queue_depth: 0 });
  assert.equal(churnRecovery.body.job.id, queued.body.id, 'an expired lease is recovered after worker churn');

  const target = await request(`${base}/admin/reliability/replicas/${manifest.manifest_id}/worker-q4`, {
    method: 'PUT',
    headers: headers(process.env.II_ADMIN_API_TOKEN),
    body: JSON.stringify({ target_replicas: 5, min_regions: 5 }),
  });
  assert.equal(target.response.status, 200);
  const replicaStatus = await request(`${base}/reliability/replicas`);
  assert.equal(replicaStatus.body.targets[0].live_replicas, 4);
  assert.equal(replicaStatus.body.targets[0].replica_deficit, 1);
  assert.equal(replicaStatus.body.targets[0].region_deficit, 1);
});
