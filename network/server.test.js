'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
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

const temporaryDirectory = require('node:fs').mkdtempSync(path.join(os.tmpdir(), 'ii-network-test-'));
const releaseKeys = crypto.generateKeyPairSync('ed25519');
process.env.II_DB_PATH = path.join(temporaryDirectory, 'network.db');
process.env.ADMIN_EXPORT_TOKEN = 'test-export-token-32-bytes-long';
process.env.II_ADMIN_API_TOKEN = 'test-admin-token-32-bytes-long';
process.env.II_JOB_API_TOKEN = 'test-job-token-32-bytes-long';
process.env.II_TEACHING_API_TOKEN = 'test-teaching-token-32-bytes-long';
process.env.II_DATA_ENCRYPTION_KEY = crypto.randomBytes(32).toString('base64');
process.env.II_RELEASE_PUBLIC_KEY = encodePublicKey(releaseKeys.publicKey);
const { app, db, assignedExperts } = require('./server');

function listen(server) {
  return new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      server.off('error', reject);
      resolve(server.address());
    });
  });
}

async function jsonRequest(url, options = {}) {
  const response = await fetch(url, options);
  const body = response.status === 204 ? null : await response.json();
  return { response, body };
}

function createHost() {
  const generated = generateHostKeyPair();
  return {
    ...generated,
    privateKey: crypto.createPrivateKey(generated.privateKeyPem),
    lastSignedAt: 0,
  };
}

function signedHostBody(host, action, payload = {}, includePublicKey = false) {
  host.lastSignedAt = Math.max(Date.now(), host.lastSignedAt + 1);
  return signHostPayload(host.privateKey, action, {
    ...payload,
    device_id: host.deviceId,
    ...(includePublicKey ? { public_key: host.publicKey } : {}),
  }, host.lastSignedAt);
}

function makeManifest(overrides = {}) {
  return signManifest({
    schema: 'ii.model-manifest.v1',
    model_id: overrides.model_id || 'ii/test-worker',
    version: overrides.version || '1.0.0',
    role: overrides.role || 'worker',
    runtime: overrides.runtime || 'node-llama-cpp@3.5.0',
    license: { id: 'Apache-2.0', redistribution_confirmed: true },
    created_at: '2026-10-22T00:00:00.000Z',
    artifacts: overrides.artifacts || [{
      id: 'worker-q4',
      sha256: 'a'.repeat(64),
      size_bytes: 10 * 1024 * 1024,
      format: 'gguf',
      url: 'https://models.example/worker-q4.gguf',
    }],
  }, releaseKeys.privateKey, releaseKeys.publicKey);
}

function claimFor(manifest, artifactId, inferenceReady = true) {
  const artifact = manifest.artifacts.find((item) => item.id === artifactId);
  return {
    manifest_id: manifest.manifest_id,
    artifact_id: artifact.id,
    sha256: artifact.sha256,
    size_bytes: artifact.size_bytes,
    format: artifact.format,
    ...(artifact.expert_id !== undefined ? { expert_id: artifact.expert_id } : {}),
    inference_ready: inferenceReady,
  };
}

test('400 deterministic placement assignments provide 20 target copies', () => {
  const replicas = Array(256).fill(0);
  for (let hostNumber = 1; hostNumber <= 400; hostNumber += 1) {
    const experts = assignedExperts(hostNumber);
    assert.equal(experts.length, 13);
    for (const expertId of experts) replicas[expertId] += 1;
  }
  assert.equal(Math.min(...replicas), 20);
  assert.equal(Math.max(...replicas), 21);
});

test('signed coordinator provides truthful routing, durable jobs, safe teaching, exports, and checkout', async (t) => {
  let capturedCheckout = null;
  const dodoServer = http.createServer(async (req, res) => {
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    capturedCheckout = {
      method: req.method,
      url: req.url,
      authorization: req.headers.authorization,
      body: JSON.parse(Buffer.concat(chunks).toString('utf8')),
    };
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({
      session_id: 'cks_test',
      checkout_url: 'https://checkout.dodopayments.com/cks_test',
    }));
  });
  const dodoAddress = await listen(dodoServer);

  process.env.DODO_PAYMENTS_API_KEY = 'test_dodo_key';
  process.env.DODO_MONTHLY_PRODUCT_ID = 'pdt_monthly';
  process.env.DODO_YEARLY_PRODUCT_ID = 'pdt_yearly';
  process.env.DODO_PAYMENTS_BASE_URL = `http://127.0.0.1:${dodoAddress.port}`;
  process.env.PUBLIC_WEB_URL = 'https://ii.example';

  const coordinator = http.createServer(app);
  const coordinatorAddress = await listen(coordinator);
  const baseUrl = `http://127.0.0.1:${coordinatorAddress.port}`;

  t.after(async () => {
    await new Promise((resolve) => coordinator.close(resolve));
    await new Promise((resolve) => dodoServer.close(resolve));
    db.close();
    await fsp.rm(temporaryDirectory, { recursive: true, force: true });
  });

  const workerManifest = makeManifest();
  const expertManifest = makeManifest({
    model_id: 'ii/test-expert',
    role: 'expert',
    version: '1.0.0',
    runtime: 'ii-remote-expert-test',
    artifacts: [{
      id: 'expert-0',
      expert_id: 0,
      sha256: 'b'.repeat(64),
      size_bytes: 10 * 1024 * 1024,
      format: 'safetensors',
      url: 'https://models.example/expert-0.safetensors',
    }],
  });
  for (const manifest of [workerManifest, expertManifest]) {
    const published = await jsonRequest(`${baseUrl}/admin/models`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: 'Bearer test-admin-token-32-bytes-long',
      },
      body: JSON.stringify(manifest),
    });
    assert.equal(published.response.status, 201);
    assert.equal(published.body.manifest_id, manifest.manifest_id);
  }

  const host = createHost();
  const artifacts = [
    claimFor(workerManifest, 'worker-q4'),
    claimFor(expertManifest, 'expert-0'),
  ];
  const firstRegistration = await jsonRequest(`${baseUrl}/register`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(signedHostBody(host, 'host:register', {
      country: 'IN',
      endpoint: 'https://host-01.example/infer',
      artifacts,
    }, true)),
  });
  assert.equal(firstRegistration.response.status, 201);
  assert.equal(firstRegistration.body.identityVerified, true);
  assert.equal(firstRegistration.body.genesisNumber, 1);
  assert.equal(firstRegistration.body.genesisMember, true);
  assert.equal(firstRegistration.body.experts_start, 0);
  assert.equal(firstRegistration.body.experts_end, 12);
  assert.equal(firstRegistration.body.artifactClaims, 2);

  const replay = await jsonRequest(`${baseUrl}/heartbeat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      ...signedHostBody(host, 'host:heartbeat', { artifacts }),
      signature: 'tampered',
    }),
  });
  assert.equal(replay.response.status, 401);
  assert.equal(replay.body.error, 'invalid_host_signature');

  const heartbeatBody = signedHostBody(host, 'host:heartbeat', {
    endpoint: 'https://host-01.example/infer',
    artifacts,
  });
  const heartbeat = await jsonRequest(`${baseUrl}/heartbeat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(heartbeatBody),
  });
  assert.equal(heartbeat.response.status, 200);
  assert.deepEqual(heartbeat.body.hostedExperts, [0]);
  const heartbeatReplay = await jsonRequest(`${baseUrl}/heartbeat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(heartbeatBody),
  });
  assert.equal(heartbeatReplay.response.status, 401);
  assert.equal(heartbeatReplay.body.error, 'invalid_host_timestamp');

  // Registration limiting still applies even to cryptographically valid hosts.
  for (let number = 2; number <= 10; number += 1) {
    const extraHost = createHost();
    const result = await fetch(`${baseUrl}/register`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(signedHostBody(extraHost, 'host:register', {}, true)),
    });
    assert.ok(result.ok);
  }
  const eleventhHost = createHost();
  const limited = await jsonRequest(`${baseUrl}/register`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(signedHostBody(eleventhHost, 'host:register', {}, true)),
  });
  assert.equal(limited.response.status, 429);

  const route = await jsonRequest(`${baseUrl}/route?expert_ids=0`);
  assert.equal(route.body.devices.length, 1);
  assert.equal(route.body.devices[0].device_id, host.deviceId);
  assert.equal(route.body.devices[0].manifest_id, expertManifest.manifest_id);
  assert.equal(route.body.devices[0].artifactVerification, 'signed_manifest_match_self_attested_runtime');
  const noRoute = await jsonRequest(`${baseUrl}/route?expert_ids=1`);
  assert.deepEqual(noRoute.body.devices, []);

  const stats = await jsonRequest(`${baseUrl}/stats`);
  assert.equal(stats.body.onlineHosts, 1);
  assert.equal(stats.body.readyWorkers, 1);
  assert.equal(stats.body.storedCoveredExperts, 1);
  assert.equal(stats.body.coveredExperts, 1);
  assert.equal(stats.body.hostedCapacityGb, 0.02);
  assert.equal(stats.body.genesisClaimed, 1, 'empty signed identities must not consume Genesis places');
  assert.match(stats.body.coverageMeaning, /manifest-matched/);

  const submitted = await jsonRequest(`${baseUrl}/jobs`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: 'Bearer test-job-token-32-bytes-long' },
    body: JSON.stringify({
      manifest_id: workerManifest.manifest_id,
      prompt: 'Give a short private background answer.',
      max_tokens: 64,
    }),
  });
  assert.equal(submitted.response.status, 202);
  const jobId = submitted.body.id;
  const jobAccessToken = submitted.body.access_token;
  assert.doesNotMatch(db.prepare('SELECT prompt_encrypted FROM jobs WHERE id = ?').get(jobId).prompt_encrypted, /private background/);

  const leased = await jsonRequest(`${baseUrl}/worker/lease`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(signedHostBody(host, 'worker:lease')),
  });
  assert.equal(leased.response.status, 200);
  assert.equal(leased.body.job.id, jobId);
  assert.equal(leased.body.job.prompt, 'Give a short private background answer.');

  const completed = await jsonRequest(`${baseUrl}/worker/jobs/${jobId}/complete`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(signedHostBody(host, 'worker:complete', {
      lease_token: leased.body.job.lease_token,
      output: 'A real worker result.',
      metrics: { duration_ms: 42 },
    })),
  });
  assert.equal(completed.body.status, 'completed');
  const job = await jsonRequest(`${baseUrl}/jobs/${jobId}`, {
    headers: { Authorization: `Bearer ${jobAccessToken}` },
  });
  assert.equal(job.body.job.result, 'A real worker result.');
  assert.deepEqual(job.body.job.metrics, { duration_ms: 42 });

  const retrySubmission = await jsonRequest(`${baseUrl}/jobs`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: 'Bearer test-job-token-32-bytes-long' },
    body: JSON.stringify({
      manifest_id: workerManifest.manifest_id,
      prompt: 'Exercise bounded lease recovery.',
      max_tokens: 16,
      max_attempts: 2,
    }),
  });
  const retryJobId = retrySubmission.body.id;
  const firstLease = await jsonRequest(`${baseUrl}/worker/lease`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(signedHostBody(host, 'worker:lease')),
  });
  assert.equal(firstLease.body.job.id, retryJobId);
  db.prepare('UPDATE jobs SET lease_expires_at = ? WHERE id = ?').run(Date.now() - 1, retryJobId);
  const secondLease = await jsonRequest(`${baseUrl}/worker/lease`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(signedHostBody(host, 'worker:lease')),
  });
  assert.equal(secondLease.body.job.id, retryJobId);
  assert.equal(secondLease.body.job.attempt, 2);
  assert.notEqual(secondLease.body.job.lease_token, firstLease.body.job.lease_token);
  const terminalFailure = await jsonRequest(`${baseUrl}/worker/jobs/${retryJobId}/fail`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(signedHostBody(host, 'worker:fail', {
      lease_token: secondLease.body.job.lease_token,
      error: 'Controlled integration failure.',
    })),
  });
  assert.equal(terminalFailure.body.status, 'failed');

  const sensitiveTeaching = await jsonRequest(`${baseUrl}/teachings`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: 'Bearer test-teaching-token-32-bytes-long' },
    body: JSON.stringify({
      manifest_id: workerManifest.manifest_id,
      prompt: 'Email me at private@example.com',
      correction: 'No.',
      consent: true,
      consent_version: '2026-10-22',
    }),
  });
  assert.equal(sensitiveTeaching.response.status, 422);
  assert.equal(db.prepare('SELECT COUNT(*) AS count FROM teachings').get().count, 0);

  const teaching = await jsonRequest(`${baseUrl}/teachings`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: 'Bearer test-teaching-token-32-bytes-long' },
    body: JSON.stringify({
      manifest_id: workerManifest.manifest_id,
      prompt: 'Explain this in Marathi.',
      correction: 'Use a simpler Marathi explanation.',
      language: 'mr-IN',
      category: 'education',
      consent: true,
      consent_version: '2026-10-22',
    }),
  });
  assert.equal(teaching.response.status, 202);
  assert.equal(teaching.body.status, 'quarantined');
  const teachingStatus = await jsonRequest(`${baseUrl}/teachings/${teaching.body.id}`, {
    headers: { Authorization: `Bearer ${teaching.body.access_token}` },
  });
  assert.equal(teachingStatus.body.teaching.status, 'quarantined');
  assert.equal('prompt' in teachingStatus.body.teaching, false);

  const application = await jsonRequest(`${baseUrl}/genesis`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email: 'host@example.com', country: 'India', acceptedHostTerms: true }),
  });
  assert.equal(application.response.status, 201);
  const deniedExport = await fetch(`${baseUrl}/admin/genesis`);
  assert.equal(deniedExport.status, 401);
  const allowedExport = await fetch(`${baseUrl}/admin/genesis`, {
    headers: { Authorization: 'Bearer test-export-token-32-bytes-long' },
  });
  assert.match(await allowedExport.text(), /host@example\.com/);

  const checkout = await fetch(`${baseUrl}/api/checkout/monthly`, { redirect: 'manual' });
  assert.equal(checkout.status, 303);
  assert.equal(checkout.headers.get('location'), 'https://checkout.dodopayments.com/cks_test');
  assert.equal(capturedCheckout.authorization, 'Bearer test_dodo_key');
  assert.deepEqual(capturedCheckout.body.product_cart, [{ product_id: 'pdt_monthly', quantity: 1 }]);

  process.env.DODO_YEARLY_URL = 'https://checkout.dodopayments.com/precreated-yearly';
  const fallback = await fetch(`${baseUrl}/api/checkout/yearly`, { redirect: 'manual' });
  assert.equal(fallback.status, 302);
  assert.equal(fallback.headers.get('location'), process.env.DODO_YEARLY_URL);

  const requeueSubmission = await jsonRequest(`${baseUrl}/jobs`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: 'Bearer test-job-token-32-bytes-long' },
    body: JSON.stringify({
      manifest_id: workerManifest.manifest_id,
      prompt: 'Requeue me when the host is revoked.',
      max_tokens: 16,
    }),
  });
  const revocationLease = await jsonRequest(`${baseUrl}/worker/lease`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(signedHostBody(host, 'worker:lease')),
  });
  assert.equal(revocationLease.body.job.id, requeueSubmission.body.id);

  const revoked = await jsonRequest(`${baseUrl}/admin/hosts/${host.deviceId}/revoke`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: 'Bearer test-admin-token-32-bytes-long' },
    body: JSON.stringify({ reason: 'Integration-test revocation.' }),
  });
  assert.equal(revoked.response.status, 200);
  assert.equal(
    db.prepare('SELECT status FROM jobs WHERE id = ?').get(requeueSubmission.body.id).status,
    'queued',
  );
  const rejectedHeartbeat = await jsonRequest(`${baseUrl}/heartbeat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(signedHostBody(host, 'host:heartbeat', { artifacts })),
  });
  assert.equal(rejectedHeartbeat.response.status, 403);
  assert.equal(rejectedHeartbeat.body.error, 'host_revoked');
});
