'use strict';

const crypto = require('node:crypto');
const {
  authenticateHost,
  bearerToken,
  createEncryptedStore,
  randomToken,
  requireConfiguredBearer,
  sendHostAuthError,
  tokenHash,
  tokenHashMatches,
} = require('./security');

const MAX_PROMPT_CHARACTERS = 8_000;
const MAX_RESULT_CHARACTERS = 100_000;
const DEFAULT_LEASE_MS = 90_000;

function cleanError(value) {
  return typeof value === 'string'
    ? value.trim().replace(/[\u0000-\u001f\u007f]/g, '').slice(0, 500)
    : 'Worker failed without an error message.';
}

function publicJob(row, encryptedStore) {
  const result = row.result_encrypted
    ? encryptedStore.decrypt(row.result_encrypted, `job:${row.id}:result`)
    : null;
  return {
    id: row.id,
    manifest_id: row.manifest_id,
    status: row.status,
    max_tokens: row.max_tokens,
    attempts: row.attempts,
    max_attempts: row.max_attempts,
    result,
    error: row.error,
    metrics: row.metrics_json ? JSON.parse(row.metrics_json) : null,
    created_at: new Date(row.created_at).toISOString(),
    updated_at: new Date(row.updated_at).toISOString(),
    completed_at: row.completed_at ? new Date(row.completed_at).toISOString() : null,
  };
}

function installJobRoutes(app, db) {
  const encryptedStore = createEncryptedStore();
  const requireSubmitter = requireConfiguredBearer('II_JOB_API_TOKEN', 'job_api_not_configured');

  function ensureEncryption(res) {
    if (encryptedStore.configured) return true;
    res.status(503).json({
      error: 'job_encryption_not_configured',
      message: 'Set II_DATA_ENCRYPTION_KEY to a random 32-byte base64 or 64-character hexadecimal key.',
    });
    return false;
  }

  app.post(['/jobs', '/api/jobs'], requireSubmitter, (req, res) => {
    if (!ensureEncryption(res)) return undefined;
    const prompt = typeof req.body?.prompt === 'string' ? req.body.prompt.trim() : '';
    const manifestId = req.body?.manifest_id;
    const requestedTokens = Number(req.body?.max_tokens ?? 256);
    const maxTokens = Number.isInteger(requestedTokens)
      ? Math.min(2_048, Math.max(1, requestedTokens))
      : null;
    const requestedAttempts = Number(req.body?.max_attempts ?? 3);
    const maxAttempts = Number.isInteger(requestedAttempts)
      ? Math.min(10, Math.max(1, requestedAttempts))
      : null;

    if (!prompt || prompt.length > MAX_PROMPT_CHARACTERS) {
      return res.status(400).json({
        error: 'invalid_prompt',
        message: `prompt must contain 1–${MAX_PROMPT_CHARACTERS} characters.`,
      });
    }
    if (!maxTokens || !maxAttempts) {
      return res.status(400).json({ error: 'invalid_job_options', message: 'Invalid token or attempt limit.' });
    }
    const manifest = db.prepare(`
      SELECT manifest_id FROM model_manifests
      WHERE manifest_id = ? AND role = 'worker' AND active = 1
    `).get(manifestId);
    if (!manifest) {
      return res.status(400).json({
        error: 'worker_manifest_required',
        message: 'manifest_id must reference an active signed worker-model release.',
      });
    }

    const now = Date.now();
    const id = `job_${crypto.randomUUID()}`;
    const accessToken = randomToken();
    db.prepare(`
      INSERT INTO jobs (
        id, manifest_id, status, prompt_encrypted, access_token_hash,
        max_tokens, max_attempts, created_at, updated_at
      ) VALUES (?, ?, 'queued', ?, ?, ?, ?, ?, ?)
    `).run(
      id,
      manifestId,
      encryptedStore.encrypt(prompt, `job:${id}:prompt`),
      tokenHash(accessToken),
      maxTokens,
      maxAttempts,
      now,
      now,
    );
    res.set('Location', `/jobs/${id}`);
    return res.status(202).json({
      ok: true,
      id,
      status: 'queued',
      access_token: accessToken,
      message: 'Save access_token now; it is not stored in recoverable form.',
    });
  });

  function loadAuthorizedJob(req, res) {
    const row = db.prepare('SELECT * FROM jobs WHERE id = ?').get(req.params.id);
    if (!row) {
      res.status(404).json({ error: 'job_not_found', message: 'Job not found.' });
      return null;
    }
    if (!tokenHashMatches(bearerToken(req), row.access_token_hash)) {
      res.set('WWW-Authenticate', 'Bearer');
      res.status(401).json({ error: 'unauthorized', message: 'The job receipt token is required.' });
      return null;
    }
    return row;
  }

  app.get(['/jobs/:id', '/api/jobs/:id'], (req, res) => {
    if (!ensureEncryption(res)) return undefined;
    const row = loadAuthorizedJob(req, res);
    return row ? res.json({ ok: true, job: publicJob(row, encryptedStore) }) : undefined;
  });

  app.post(['/jobs/:id/cancel', '/api/jobs/:id/cancel'], (req, res) => {
    if (!ensureEncryption(res)) return undefined;
    const row = loadAuthorizedJob(req, res);
    if (!row) return undefined;
    if (!['queued', 'leased'].includes(row.status)) {
      return res.status(409).json({ error: 'job_not_cancellable', message: `Job is already ${row.status}.` });
    }
    db.prepare(`
      UPDATE jobs SET status = 'cancelled', lease_device_id = NULL,
        lease_token_hash = NULL, lease_expires_at = NULL, updated_at = ?
      WHERE id = ?
    `).run(Date.now(), row.id);
    return res.json({ ok: true, id: row.id, status: 'cancelled' });
  });

  const expireLeases = db.transaction((now) => {
    db.prepare(`
      UPDATE jobs
      SET status = CASE WHEN attempts >= max_attempts THEN 'failed' ELSE 'queued' END,
          error = CASE WHEN attempts >= max_attempts THEN 'Worker lease expired too many times.' ELSE error END,
          lease_device_id = NULL,
          lease_token_hash = NULL,
          lease_expires_at = NULL,
          updated_at = ?
      WHERE status = 'leased' AND lease_expires_at < ?
    `).run(now, now);
  });

  const leaseNextJob = db.transaction((deviceId, now, leaseMs) => {
    expireLeases(now);
    const job = db.prepare(`
      SELECT j.*
      FROM jobs j
      JOIN model_manifests m ON m.manifest_id = j.manifest_id AND m.active = 1
      JOIN host_artifacts ha ON ha.manifest_id = j.manifest_id
        AND ha.device_id = ? AND ha.inference_ready = 1
      WHERE j.status = 'queued' AND j.attempts < j.max_attempts
      ORDER BY j.created_at ASC
      LIMIT 1
    `).get(deviceId);
    if (!job) return null;
    const leaseToken = randomToken();
    const result = db.prepare(`
      UPDATE jobs
      SET status = 'leased', lease_device_id = ?, lease_token_hash = ?,
          lease_expires_at = ?, attempts = attempts + 1, updated_at = ?
      WHERE id = ? AND status = 'queued'
    `).run(deviceId, tokenHash(leaseToken), now + leaseMs, now, job.id);
    if (result.changes !== 1) return null;
    return {
      ...job,
      attempts: job.attempts + 1,
      lease_token: leaseToken,
      lease_expires_at: now + leaseMs,
    };
  });

  app.post(['/worker/lease', '/api/worker/lease'], (req, res) => {
    if (!ensureEncryption(res)) return undefined;
    const auth = authenticateHost(db, 'worker:lease', req.body, { consume: true });
    if (!auth.ok) return sendHostAuthError(res, auth);
    const leaseMsSetting = Number.parseInt(process.env.II_JOB_LEASE_MS || String(DEFAULT_LEASE_MS), 10);
    const leaseMs = Number.isInteger(leaseMsSetting)
      ? Math.min(10 * 60_000, Math.max(30_000, leaseMsSetting))
      : DEFAULT_LEASE_MS;
    const job = leaseNextJob(auth.deviceId, Date.now(), leaseMs);
    if (!job) return res.status(204).end();
    return res.json({
      ok: true,
      job: {
        id: job.id,
        manifest_id: job.manifest_id,
        prompt: encryptedStore.decrypt(job.prompt_encrypted, `job:${job.id}:prompt`),
        max_tokens: job.max_tokens,
        attempt: job.attempts,
        lease_token: job.lease_token,
        lease_expires_at: new Date(job.lease_expires_at).toISOString(),
      },
    });
  });

  function loadWorkerLease(req, res, action) {
    const auth = authenticateHost(db, action, req.body, { consume: true });
    if (!auth.ok) {
      sendHostAuthError(res, auth);
      return null;
    }
    const job = db.prepare('SELECT * FROM jobs WHERE id = ?').get(req.params.id);
    if (!job) {
      res.status(404).json({ error: 'job_not_found', message: 'Job not found.' });
      return null;
    }
    if (job.status !== 'leased'
        || job.lease_device_id !== auth.deviceId
        || job.lease_expires_at < Date.now()
        || !tokenHashMatches(req.body.lease_token, job.lease_token_hash)) {
      res.status(409).json({ error: 'invalid_lease', message: 'The worker lease is absent, expired, or invalid.' });
      return null;
    }
    return { auth, job };
  }

  app.post(['/worker/jobs/:id/complete', '/api/worker/jobs/:id/complete'], (req, res) => {
    if (!ensureEncryption(res)) return undefined;
    const lease = loadWorkerLease(req, res, 'worker:complete');
    if (!lease) return undefined;
    const output = typeof req.body.output === 'string' ? req.body.output.trim() : '';
    if (!output || output.length > MAX_RESULT_CHARACTERS) {
      return res.status(400).json({
        error: 'invalid_result',
        message: `output must contain 1–${MAX_RESULT_CHARACTERS} characters.`,
      });
    }
    const metrics = req.body.metrics && typeof req.body.metrics === 'object'
      ? JSON.stringify(req.body.metrics).slice(0, 4_000)
      : null;
    const now = Date.now();
    db.prepare(`
      UPDATE jobs
      SET status = 'completed', result_encrypted = ?, metrics_json = ?, error = NULL,
          lease_token_hash = NULL, lease_expires_at = NULL, updated_at = ?, completed_at = ?
      WHERE id = ?
    `).run(
      encryptedStore.encrypt(output, `job:${lease.job.id}:result`),
      metrics,
      now,
      now,
      lease.job.id,
    );
    return res.json({ ok: true, id: lease.job.id, status: 'completed' });
  });

  app.post(['/worker/jobs/:id/fail', '/api/worker/jobs/:id/fail'], (req, res) => {
    if (!ensureEncryption(res)) return undefined;
    const lease = loadWorkerLease(req, res, 'worker:fail');
    if (!lease) return undefined;
    const terminal = lease.job.attempts >= lease.job.max_attempts;
    const status = terminal ? 'failed' : 'queued';
    db.prepare(`
      UPDATE jobs
      SET status = ?, error = ?, lease_device_id = NULL, lease_token_hash = NULL,
          lease_expires_at = NULL, updated_at = ?
      WHERE id = ?
    `).run(status, cleanError(req.body.error), Date.now(), lease.job.id);
    return res.json({ ok: true, id: lease.job.id, status, retrying: !terminal });
  });
}

module.exports = { installJobRoutes, publicJob };
