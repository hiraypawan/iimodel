'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const fsp = require('node:fs/promises');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');
const { canonicalJson, encodePublicKey, decodePublicKey } = require('../protocol/crypto');
const { signManifest } = require('../protocol/model-manifest');

const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'ii-learning-test-'));
const releaseKeys = crypto.generateKeyPairSync('ed25519');
const adapterKeys = crypto.generateKeyPairSync('ed25519');
process.env.II_DB_PATH = path.join(directory, 'network.db');
process.env.II_DATA_ENCRYPTION_KEY = crypto.randomBytes(32).toString('base64');
process.env.II_ADMIN_API_TOKEN = 'learning-admin-token-32-bytes';
process.env.II_TEACHING_API_TOKEN = 'learning-submit-token-32-bytes';
process.env.II_RELEASE_PUBLIC_KEY = encodePublicKey(releaseKeys.publicKey);
process.env.II_ADAPTER_SIGNING_PRIVATE_KEY = adapterKeys.privateKey.export({ format: 'pem', type: 'pkcs8' });
const { app, db } = require('./server');

function listen(server) {
  return new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => resolve(server.address()));
  });
}

async function request(url, options = {}) {
  const response = await fetch(url, options);
  const body = await response.json();
  return { response, body };
}

function headers(token) {
  return { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` };
}

test('reviewed teaching becomes a gated signed adapter and withdrawal stops rollout', async (t) => {
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
    model_id: 'ii/learning-base',
    version: '1.0.0',
    role: 'worker',
    runtime: 'node-llama-cpp@3.5.0',
    license: { id: 'Apache-2.0', redistribution_confirmed: true },
    created_at: '2026-10-23T00:00:00.000Z',
    artifacts: [{
      id: 'base-q4', sha256: 'd'.repeat(64), size_bytes: 10 * 1024 * 1024,
      format: 'gguf', url: 'https://models.example/base.gguf',
    }],
  }, releaseKeys.privateKey, releaseKeys.publicKey);
  assert.equal((await request(`${base}/admin/models`, {
    method: 'POST', headers: headers(process.env.II_ADMIN_API_TOKEN), body: JSON.stringify(manifest),
  })).response.status, 201);

  const teaching = await request(`${base}/api/teachings`, {
    method: 'POST',
    headers: headers(process.env.II_TEACHING_API_TOKEN),
    body: JSON.stringify({
      manifest_id: manifest.manifest_id,
      prompt: 'Explain monsoon formation in concise Marathi.',
      correction: 'Use simple Marathi and distinguish winds from rainfall.',
      language: 'mr-IN',
      category: 'education',
      consent: true,
      consent_version: '2026-10-22',
    }),
  });
  assert.equal(teaching.response.status, 202);

  const content = await request(`${base}/admin/teachings/${teaching.body.id}/content`, {
    headers: { Authorization: `Bearer ${process.env.II_ADMIN_API_TOKEN}`, 'x-reviewer-id': 'reviewer-1' },
  });
  assert.equal(content.body.teaching.privacy_scan.passed, true);
  assert.match(content.body.teaching.correction, /simple Marathi/);

  const reviewed = await request(`${base}/admin/teachings/${teaching.body.id}/review`, {
    method: 'POST',
    headers: headers(process.env.II_ADMIN_API_TOKEN),
    body: JSON.stringify({ decision: 'approved', reviewer_id: 'reviewer-1', notes: 'Useful and source-safe.' }),
  });
  assert.equal(reviewed.body.status, 'approved');

  const dataset = await request(`${base}/admin/datasets`, {
    method: 'POST', headers: headers(process.env.II_ADMIN_API_TOKEN),
    body: JSON.stringify({ teaching_ids: [teaching.body.id], created_by: 'dataset-curator-1' }),
  });
  assert.equal(dataset.response.status, 201);
  const sealed = await request(`${base}/admin/datasets/${dataset.body.id}/seal`, {
    method: 'POST', headers: headers(process.env.II_ADMIN_API_TOKEN),
    body: JSON.stringify({ approved_by: 'dataset-curator-2' }),
  });
  assert.match(sealed.body.content_hash, /^[a-f0-9]{64}$/);

  const run = await request(`${base}/admin/training-runs`, {
    method: 'POST', headers: headers(process.env.II_ADMIN_API_TOKEN),
    body: JSON.stringify({
      dataset_id: dataset.body.id,
      orchestrator: 'external-gpu-runner-v1',
      config: { method: 'lora', rank: 8, epochs: 2, seed: 42 },
      environment: { trainer_version: '1.0.0', cuda: 'external-evidence-required' },
      requested_by: 'trainer-operator',
    }),
  });
  assert.equal(run.response.status, 202);
  assert.equal(run.body.execution, 'external_orchestrator_required');

  const deniedReport = await request(`${base}/admin/training-runs/${run.body.id}/report`, {
    method: 'POST', headers: headers(process.env.II_ADMIN_API_TOKEN),
    body: JSON.stringify({
      status: 'awaiting_approval',
      metrics: { benchmark_passed: false },
      privacy_report: { passed: true },
      artifact_sha256: 'e'.repeat(64),
      artifact_size_bytes: 1024,
      artifact_url: 'https://models.example/adapter.bin',
    }),
  });
  assert.equal(deniedReport.response.status, 422);

  const reported = await request(`${base}/admin/training-runs/${run.body.id}/report`, {
    method: 'POST', headers: headers(process.env.II_ADMIN_API_TOKEN),
    body: JSON.stringify({
      status: 'awaiting_approval',
      metrics: { benchmark_passed: true, quality_delta: 0.04, regressions: 0 },
      privacy_report: { passed: true, memorization_findings: 0 },
      artifact_sha256: 'e'.repeat(64),
      artifact_size_bytes: 1_048_576,
      artifact_url: 'https://models.example/adapter.bin',
      reported_by: 'external-runner-1',
    }),
  });
  assert.equal(reported.response.status, 200);
  const approved = await request(`${base}/admin/training-runs/${run.body.id}/approve`, {
    method: 'POST', headers: headers(process.env.II_ADMIN_API_TOKEN),
    body: JSON.stringify({ approved_by: 'release-reviewer-1' }),
  });
  assert.equal(approved.body.status, 'approved');

  const published = await request(`${base}/admin/training-runs/${run.body.id}/publish`, {
    method: 'POST', headers: headers(process.env.II_ADMIN_API_TOKEN),
    body: JSON.stringify({ published_by: 'release-operator-1' }),
  });
  assert.equal(published.response.status, 201);
  assert.equal(published.body.status, 'staged');
  assert.equal(
    crypto.verify(
      null,
      Buffer.from(canonicalJson(published.body.manifest)),
      decodePublicKey(published.body.signer_public_key),
      Buffer.from(published.body.signature, 'base64'),
    ),
    true,
  );

  const rollout = await request(`${base}/admin/adapters/${published.body.id}/rollout`, {
    method: 'POST', headers: headers(process.env.II_ADMIN_API_TOKEN),
    body: JSON.stringify({ percent: 10, approved_by: 'release-reviewer-2' }),
  });
  assert.equal(rollout.body.rollout_percent, 10);

  const withdrawn = await request(`${base}/api/teachings/${teaching.body.id}`, {
    method: 'DELETE',
    headers: { Authorization: `Bearer ${teaching.body.access_token}` },
  });
  assert.equal(withdrawn.body.deleted, true);
  assert.match(withdrawn.body.affected.limitation, /cannot be selectively untrained/);
  assert.equal(
    db.prepare('SELECT status FROM adapter_releases WHERE id = ?').get(published.body.id).status,
    'withdrawn',
  );
  assert.equal(
    db.prepare('SELECT rollout_percent FROM adapter_releases WHERE id = ?').get(published.body.id).rollout_percent,
    0,
  );
  assert.ok(db.prepare('SELECT COUNT(*) AS count FROM audit_events').get().count >= 10);
});
