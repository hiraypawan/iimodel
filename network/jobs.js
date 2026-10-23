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
const { findUserByBearer } = require('./accounts');
const {
  decayedScore,
  normalizeCapabilities,
  normalizeRegion,
  recordReputationEvent,
  upsertWorkerCapabilities,
} = require('./reliability');

const MAX_PROMPT_CHARACTERS = 8_000;
const MAX_RESULT_CHARACTERS = 100_000;
const DEFAULT_LEASE_MS = 90_000;
const DEFAULT_HEDGE_AFTER_MS = 2_000;

function cleanError(value) {
  return typeof value === 'string'
    ? value.trim().replace(/[\u0000-\u001f\u007f]/g, '').slice(0, 500)
    : 'Worker failed without an error message.';
}

function normalizedOutput(value) {
  return String(value || '').normalize('NFKC').trim().replace(/\s+/g, ' ');
}

function outputHash(value) {
  return crypto.createHash('sha256').update(normalizedOutput(value), 'utf8').digest('hex');
}

function normalizeMetrics(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const bounded = {
    duration_ms: [0, 3_600_000],
    prompt_tokens: [0, 10_000_000],
    output_tokens: [0, 10_000_000],
    tokens_per_second: [0, 1_000_000],
    time_to_first_token_ms: [0, 3_600_000],
  };
  const metrics = {};
  for (const [key, [minimum, maximum]] of Object.entries(bounded)) {
    const number = Number(value[key]);
    if (Number.isFinite(number) && number >= minimum && number <= maximum) {
      metrics[key] = Math.round(number * 1000) / 1000;
    }
  }
  return Object.keys(metrics).length ? metrics : null;
}

function publicJob(row, encryptedStore, attemptSummary = null) {
  const result = row.result_encrypted
    ? encryptedStore.decrypt(row.result_encrypted, `job:${row.id}:result`)
    : null;
  return {
    id: row.id,
    manifest_id: row.manifest_id,
    status: row.status,
    kind: row.job_kind || 'inference',
    max_tokens: row.max_tokens,
    attempts: row.attempts,
    max_attempts: row.max_attempts,
    required_results: row.required_results || 1,
    hedging: {
      enabled: (row.max_parallel_attempts || 1) > 1,
      delay_ms: row.hedge_after_ms || DEFAULT_HEDGE_AFTER_MS,
      max_parallel_attempts: row.max_parallel_attempts || 1,
    },
    desired_region: row.desired_region || null,
    comparison_status: row.comparison_status || 'not_requested',
    result,
    error: row.error,
    metrics: row.metrics_json ? JSON.parse(row.metrics_json) : null,
    attempt_summary: attemptSummary ? {
      total: Number(attemptSummary.total || 0),
      active: Number(attemptSummary.active || 0),
      completed: Number(attemptSummary.completed || 0),
      cancelled: Number(attemptSummary.cancelled || 0),
      hedged: Number(attemptSummary.hedged || 0),
    } : null,
    created_at: new Date(row.created_at).toISOString(),
    updated_at: new Date(row.updated_at).toISOString(),
    completed_at: row.completed_at ? new Date(row.completed_at).toISOString() : null,
  };
}

function installJobRoutes(app, db) {
  const encryptedStore = createEncryptedStore();
  const requireServiceSubmitter = requireConfiguredBearer('II_JOB_API_TOKEN', 'job_api_not_configured');
  const requireSubmitter = (req, res, next) => {
    const user = findUserByBearer(db, req, { scope: 'jobs:submit' });
    if (user) {
      req.user = user;
      return next();
    }
    return requireServiceSubmitter(req, res, next);
  };
  const requireAdmin = requireConfiguredBearer('II_ADMIN_API_TOKEN', 'admin_api_not_configured');

  function ensureEncryption(res) {
    if (encryptedStore.configured) return true;
    res.status(503).json({
      error: 'job_encryption_not_configured',
      message: 'Set II_DATA_ENCRYPTION_KEY to a random 32-byte base64 or 64-character hexadecimal key.',
    });
    return false;
  }

  function activeWorkerManifest(manifestId) {
    return db.prepare(`
      SELECT manifest_id FROM model_manifests
      WHERE manifest_id = ? AND role = 'worker' AND active = 1
    `).get(manifestId);
  }

  function insertJob({
    manifestId,
    prompt,
    maxTokens,
    maxAttempts,
    requiredResults = 1,
    desiredRegion = null,
    hedgeAfterMs = DEFAULT_HEDGE_AFTER_MS,
    maxParallelAttempts = 2,
    kind = 'inference',
    expectedOutputHash = null,
    priority = 0,
    userId = null,
  }) {
    const now = Date.now();
    const id = `job_${crypto.randomUUID()}`;
    const accessToken = randomToken();
    db.prepare(`
      INSERT INTO jobs (
        id, manifest_id, status, prompt_encrypted, access_token_hash,
        max_tokens, max_attempts, job_kind, expected_output_hash,
        required_results, desired_region, hedge_after_ms,
        max_parallel_attempts, comparison_status, priority, user_id,
        created_at, updated_at
      ) VALUES (?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    `).run(
      id,
      manifestId,
      encryptedStore.encrypt(prompt, `job:${id}:prompt`),
      tokenHash(accessToken),
      maxTokens,
      maxAttempts,
      kind,
      expectedOutputHash,
      requiredResults,
      desiredRegion,
      hedgeAfterMs,
      maxParallelAttempts,
      requiredResults === 2 ? 'pending' : 'not_requested',
      priority,
      userId,
      now,
      now,
    );
    return { id, accessToken };
  }

  function userQuota(userId, maxTokens, now = Date.now()) {
    const rows = db.prepare(`
      SELECT * FROM entitlements
      WHERE user_id = ? AND feature IN ('inference_jobs', 'inference_tokens')
        AND status = 'active' AND period_start <= ? AND period_end > ?
      ORDER BY period_start DESC
    `).all(userId, now, now);
    const byFeature = new Map();
    for (const row of rows) if (!byFeature.has(row.feature)) byFeature.set(row.feature, row);
    const jobs = byFeature.get('inference_jobs');
    const tokens = byFeature.get('inference_tokens');
    if (!jobs || !tokens || jobs.used + 1 > jobs.allowance || tokens.used + maxTokens > tokens.allowance) {
      return null;
    }
    return { jobs, tokens };
  }

  function consumeUserQuota(userId, jobId, maxTokens, quota, now = Date.now()) {
    const consume = db.prepare(`
      UPDATE entitlements SET used = used + ?, updated_at = ?
      WHERE user_id = ? AND feature = ? AND period_start = ?
        AND status = 'active' AND used + ? <= allowance
    `);
    const jobs = consume.run(1, now, userId, 'inference_jobs', quota.jobs.period_start, 1);
    const tokens = consume.run(maxTokens, now, userId, 'inference_tokens', quota.tokens.period_start, maxTokens);
    if (jobs.changes !== 1 || tokens.changes !== 1) throw new Error('User quota changed concurrently.');
    const usage = db.prepare(`
      INSERT INTO usage_events (
        user_id, feature, quantity, idempotency_key, job_id, created_at
      ) VALUES (?, ?, ?, ?, ?, ?)
    `);
    usage.run(userId, 'inference_jobs', 1, `${jobId}:jobs`, jobId, now);
    usage.run(userId, 'inference_tokens', maxTokens, `${jobId}:tokens`, jobId, now);
  }

  app.post(['/jobs', '/api/jobs'], requireSubmitter, (req, res) => {
    if (!ensureEncryption(res)) return undefined;
    const prompt = typeof req.body?.prompt === 'string' ? req.body.prompt.trim() : '';
    const manifestId = req.body?.manifest_id;
    const requestedTokens = Number(req.body?.max_tokens ?? 256);
    const maxTokens = Number.isInteger(requestedTokens)
      ? Math.min(2_048, Math.max(1, requestedTokens))
      : null;
    const requiredResults = req.body?.verification === 'duplicate' ? 2 : 1;
    const requestedAttempts = Number(req.body?.max_attempts ?? Math.max(3, requiredResults));
    const maxAttempts = Number.isInteger(requestedAttempts)
      ? Math.min(10, Math.max(requiredResults, requestedAttempts))
      : null;
    const hedgeAfter = Number(req.body?.hedge_after_ms ?? DEFAULT_HEDGE_AFTER_MS);
    const hedgeAfterMs = Number.isInteger(hedgeAfter)
      ? Math.min(60_000, Math.max(250, hedgeAfter))
      : null;
    const desiredRegion = req.body?.desired_region === undefined
      ? null
      : normalizeRegion(req.body.desired_region);

    if (!prompt || prompt.length > MAX_PROMPT_CHARACTERS) {
      return res.status(400).json({
        error: 'invalid_prompt',
        message: `prompt must contain 1–${MAX_PROMPT_CHARACTERS} characters.`,
      });
    }
    if (!maxTokens || !maxAttempts || !hedgeAfterMs
        || (req.body?.desired_region !== undefined && !desiredRegion)) {
      return res.status(400).json({
        error: 'invalid_job_options',
        message: 'Invalid token, attempt, hedge, verification, or declared-region option.',
      });
    }
    if (!activeWorkerManifest(manifestId)) {
      return res.status(400).json({
        error: 'worker_manifest_required',
        message: 'manifest_id must reference an active signed worker-model release.',
      });
    }

    const quota = req.user ? userQuota(req.user.id, maxTokens) : null;
    if (req.user && !quota) {
      return res.status(429).json({
        error: 'quota_exhausted',
        message: 'The active inference job or token entitlement is exhausted.',
      });
    }
    const createWithQuota = db.transaction(() => {
      const createdJob = insertJob({
        manifestId,
        prompt,
        maxTokens,
        maxAttempts,
        requiredResults,
        desiredRegion,
        hedgeAfterMs,
        maxParallelAttempts: 2,
        userId: req.user?.id || null,
      });
      if (req.user) consumeUserQuota(req.user.id, createdJob.id, maxTokens, quota);
      return createdJob;
    });
    const created = createWithQuota();
    res.set('Location', `/jobs/${created.id}`);
    return res.status(202).json({
      ok: true,
      id: created.id,
      status: 'queued',
      access_token: created.accessToken,
      verification: requiredResults === 2 ? 'duplicate' : 'first_valid_result',
      message: 'Save access_token now; it is not stored in recoverable form.',
    });
  });

  app.post('/admin/challenges', requireAdmin, (req, res) => {
    if (!ensureEncryption(res)) return undefined;
    const prompt = typeof req.body?.prompt === 'string' ? req.body.prompt.trim() : '';
    const expectedOutput = typeof req.body?.expected_output === 'string'
      ? req.body.expected_output.trim()
      : '';
    const manifestId = req.body?.manifest_id;
    const maxTokens = Number.isInteger(req.body?.max_tokens)
      ? Math.min(2_048, Math.max(1, req.body.max_tokens))
      : 64;
    if (!prompt || prompt.length > MAX_PROMPT_CHARACTERS
        || !expectedOutput || expectedOutput.length > MAX_RESULT_CHARACTERS
        || !activeWorkerManifest(manifestId)) {
      return res.status(400).json({
        error: 'invalid_challenge',
        message: 'A bounded prompt, expected_output, and active worker manifest are required.',
      });
    }
    const created = insertJob({
      manifestId,
      prompt,
      maxTokens,
      maxAttempts: 3,
      requiredResults: 1,
      hedgeAfterMs: DEFAULT_HEDGE_AFTER_MS,
      maxParallelAttempts: 1,
      kind: 'challenge',
      expectedOutputHash: outputHash(expectedOutput),
      priority: 50,
    });
    return res.status(201).json({
      ok: true,
      id: created.id,
      expected_output_sha256: outputHash(expectedOutput),
      deterministic_options: { temperature: 0, seed: 0 },
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
    if (!row) return undefined;
    const attemptSummary = db.prepare(`
      SELECT
        COUNT(*) AS total,
        SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) AS active,
        SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed,
        SUM(CASE WHEN status = 'cancelled' THEN 1 ELSE 0 END) AS cancelled,
        SUM(CASE WHEN is_hedge = 1 THEN 1 ELSE 0 END) AS hedged
      FROM job_attempts WHERE job_id = ?
    `).get(row.id);
    return res.json({ ok: true, job: publicJob(row, encryptedStore, attemptSummary) });
  });

  app.post(['/jobs/:id/cancel', '/api/jobs/:id/cancel'], (req, res) => {
    if (!ensureEncryption(res)) return undefined;
    const row = loadAuthorizedJob(req, res);
    if (!row) return undefined;
    if (!['queued', 'leased'].includes(row.status)) {
      return res.status(409).json({ error: 'job_not_cancellable', message: `Job is already ${row.status}.` });
    }
    const now = Date.now();
    const cancel = db.transaction(() => {
      db.prepare(`
        UPDATE job_attempts SET status = 'cancelled', completed_at = ?
        WHERE job_id = ? AND status = 'active'
      `).run(now, row.id);
      db.prepare(`
        UPDATE jobs SET status = 'cancelled', lease_device_id = NULL,
          lease_token_hash = NULL, lease_expires_at = NULL, updated_at = ?
        WHERE id = ?
      `).run(now, row.id);
    });
    cancel();
    return res.json({ ok: true, id: row.id, status: 'cancelled' });
  });

  const expireAttempts = db.transaction((now) => {
    const expired = db.prepare(`
      SELECT ja.*, j.manifest_id
      FROM job_attempts ja JOIN jobs j ON j.id = ja.job_id
      WHERE ja.status = 'active' AND ja.lease_expires_at < ?
    `).all(now);
    const affected = new Set();
    for (const attempt of expired) {
      db.prepare(`
        UPDATE job_attempts SET status = 'expired', error = ?, completed_at = ?
        WHERE id = ? AND status = 'active'
      `).run('Worker lease expired.', now, attempt.id);
      recordReputationEvent(db, {
        deviceId: attempt.device_id,
        manifestId: attempt.manifest_id,
        jobId: attempt.job_id,
        attemptId: attempt.id,
        eventType: 'expired',
        reason: 'Worker lease expired before a result was submitted.',
        now,
      });
      affected.add(attempt.job_id);
    }
    for (const jobId of affected) {
      const job = db.prepare('SELECT * FROM jobs WHERE id = ?').get(jobId);
      const counts = db.prepare(`
        SELECT SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) AS active,
               SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed
        FROM job_attempts WHERE job_id = ?
      `).get(jobId);
      if ((counts.active || 0) === 0 && job.status === 'leased') {
        const terminal = job.attempts >= job.max_attempts;
        db.prepare(`
          UPDATE jobs SET status = ?, error = ?, lease_device_id = NULL,
            lease_token_hash = NULL, lease_expires_at = NULL, updated_at = ?
          WHERE id = ?
        `).run(
          terminal ? 'failed' : 'queued',
          terminal ? 'Worker lease expired too many times.' : job.error,
          now,
          jobId,
        );
      }
    }
    return expired.length;
  });

  function workerCapacity(deviceId, now) {
    const capability = db.prepare(`
      SELECT wc.*, d.country
      FROM devices d LEFT JOIN worker_capabilities wc ON wc.device_id = d.id
      WHERE d.id = ?
    `).get(deviceId);
    if (!capability) return null;
    const active = db.prepare(`
      SELECT COUNT(*) AS count FROM job_attempts
      WHERE device_id = ? AND status = 'active' AND lease_expires_at >= ?
    `).get(deviceId, now).count;
    const maxConcurrency = capability.max_concurrency || 1;
    if (active >= maxConcurrency || (capability.queue_depth || 0) >= maxConcurrency) return null;
    return {
      region: capability.region || capability.country || null,
      available: maxConcurrency - active,
    };
  }

  function leaseCandidate(deviceId, now) {
    const candidates = db.prepare(`
      SELECT DISTINCT j.*
      FROM jobs j
      JOIN model_manifests m ON m.manifest_id = j.manifest_id AND m.active = 1
      JOIN host_artifacts ha ON ha.manifest_id = j.manifest_id
        AND ha.device_id = ? AND ha.inference_ready = 1
      WHERE j.status IN ('queued', 'leased') AND j.attempts < j.max_attempts
      ORDER BY j.priority DESC, j.created_at ASC
      LIMIT 100
    `).all(deviceId);
    const capacity = workerCapacity(deviceId, now);
    if (!capacity) return null;
    const eligible = [];
    for (const job of candidates) {
      if (job.desired_region && capacity.region !== job.desired_region) {
        const preferredAvailable = db.prepare(`
          SELECT 1 AS found
          FROM devices d
          JOIN host_artifacts ha ON ha.device_id = d.id
            AND ha.manifest_id = ? AND ha.inference_ready = 1
          LEFT JOIN worker_capabilities wc ON wc.device_id = d.id
          LEFT JOIN worker_manifest_scores wms
            ON wms.device_id = d.id AND wms.manifest_id = ?
          WHERE COALESCE(wc.region, d.country) = ?
            AND d.status = 'online' AND d.last_heartbeat >= ?
            AND (wms.quarantine_until IS NULL OR wms.quarantine_until <= ?)
            AND COALESCE(wc.queue_depth, 0) < COALESCE(wc.max_concurrency, 1)
            AND (
              SELECT COUNT(*) FROM job_attempts active_attempt
              WHERE active_attempt.device_id = d.id AND active_attempt.status = 'active'
                AND active_attempt.lease_expires_at >= ?
            ) < COALESCE(wc.max_concurrency, 1)
          LIMIT 1
        `).get(
          job.manifest_id,
          job.manifest_id,
          job.desired_region,
          now - 60_000,
          now,
          now,
        );
        if (preferredAvailable) continue;
      }
      const score = db.prepare(`
        SELECT * FROM worker_manifest_scores WHERE device_id = ? AND manifest_id = ?
      `).get(deviceId, job.manifest_id);
      if (score?.quarantine_until && score.quarantine_until > now) continue;
      const attempts = db.prepare(`
        SELECT * FROM job_attempts WHERE job_id = ? ORDER BY leased_at ASC
      `).all(job.id);
      if (attempts.some((attempt) => attempt.device_id === deviceId
          && ['active', 'completed'].includes(attempt.status))) continue;
      const active = attempts.filter((attempt) => attempt.status === 'active');
      const completed = attempts.filter((attempt) => attempt.status === 'completed');
      let isHedge = false;
      let allowed = false;
      if (job.status === 'queued') {
        allowed = active.length === 0;
      } else if (completed.length < (job.required_results || 1)
          && active.length < Math.min(2, job.max_parallel_attempts || 1)) {
        if (completed.length > 0 && active.length === 0) {
          allowed = true;
          isHedge = true;
        } else if (active.length > 0) {
          const oldestLease = Math.min(...active.map((attempt) => attempt.leased_at));
          allowed = oldestLease <= now - (job.hedge_after_ms || DEFAULT_HEDGE_AFTER_MS);
          isHedge = allowed;
        }
      }
      if (!allowed) continue;
      eligible.push({
        job,
        isHedge,
        regionMatch: Boolean(job.desired_region && capacity.region === job.desired_region),
        score: score ? decayedScore(score.score, score.last_event_at, now) : 0.5,
      });
    }
    eligible.sort((left, right) => (
      Number(right.regionMatch) - Number(left.regionMatch)
      || right.job.priority - left.job.priority
      || right.score - left.score
      || left.job.created_at - right.job.created_at
    ));
    return eligible[0] || null;
  }

  const leaseNextJob = db.transaction((deviceId, now, leaseMs) => {
    expireAttempts(now);
    const candidate = leaseCandidate(deviceId, now);
    if (!candidate) return null;
    const { job, isHedge } = candidate;
    const leaseToken = randomToken();
    const attemptId = `attempt_${crypto.randomUUID()}`;
    const expiresAt = now + leaseMs;
    db.prepare(`
      INSERT INTO job_attempts (
        id, job_id, device_id, status, lease_token_hash, lease_expires_at,
        is_hedge, leased_at
      ) VALUES (?, ?, ?, 'active', ?, ?, ?, ?)
    `).run(
      attemptId,
      job.id,
      deviceId,
      tokenHash(leaseToken),
      expiresAt,
      isHedge ? 1 : 0,
      now,
    );
    db.prepare(`
      UPDATE jobs
      SET status = 'leased', lease_device_id = ?, lease_token_hash = ?,
          lease_expires_at = ?, attempts = attempts + 1, updated_at = ?
      WHERE id = ? AND status IN ('queued', 'leased')
    `).run(deviceId, tokenHash(leaseToken), expiresAt, now, job.id);
    return {
      ...job,
      attempts: job.attempts + 1,
      attempt_id: attemptId,
      is_hedge: isHedge,
      lease_token: leaseToken,
      lease_expires_at: expiresAt,
    };
  });

  app.post(['/worker/lease', '/api/worker/lease'], (req, res) => {
    if (!ensureEncryption(res)) return undefined;
    const auth = authenticateHost(db, 'worker:lease', req.body, { consume: true });
    if (!auth.ok) return sendHostAuthError(res, auth);
    if (req.body?.capabilities && typeof req.body.capabilities === 'object') {
      // Lease-time load is signed and fresher than the periodic heartbeat. The
      // hardware fields remain self-declared scheduling hints.
      const country = db.prepare('SELECT country FROM devices WHERE id = ?').get(auth.deviceId)?.country;
      upsertWorkerCapabilities(
        db,
        auth.deviceId,
        normalizeCapabilities(req.body.capabilities, country),
        Date.now(),
      );
    }
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
        attempt_id: job.attempt_id,
        is_hedge: Boolean(job.is_hedge),
        kind: job.job_kind || 'inference',
        manifest_id: job.manifest_id,
        prompt: encryptedStore.decrypt(job.prompt_encrypted, `job:${job.id}:prompt`),
        max_tokens: job.max_tokens,
        attempt: job.attempts,
        deterministic: job.job_kind === 'challenge',
        temperature: job.job_kind === 'challenge' ? 0 : undefined,
        seed: job.job_kind === 'challenge' ? 0 : undefined,
        lease_token: job.lease_token,
        lease_expires_at: new Date(job.lease_expires_at).toISOString(),
      },
    });
  });

  function loadWorkerAttempt(req, res, action, { allowInactive = false } = {}) {
    const auth = authenticateHost(db, action, req.body, { consume: true });
    if (!auth.ok) {
      sendHostAuthError(res, auth);
      return null;
    }
    const job = db.prepare('SELECT * FROM jobs WHERE id = ?').get(req.params.id || req.body.job_id);
    if (!job) {
      res.status(404).json({ error: 'job_not_found', message: 'Job not found.' });
      return null;
    }
    const attempt = req.body.attempt_id
      ? db.prepare('SELECT * FROM job_attempts WHERE id = ? AND job_id = ?').get(req.body.attempt_id, job.id)
      : db.prepare(`
        SELECT * FROM job_attempts
        WHERE job_id = ? AND device_id = ? AND status = 'active'
        ORDER BY leased_at DESC LIMIT 1
      `).get(job.id, auth.deviceId);
    if (!attempt || attempt.device_id !== auth.deviceId
        || !tokenHashMatches(req.body.lease_token, attempt.lease_token_hash)) {
      res.status(409).json({ error: 'invalid_lease', message: 'The worker attempt or lease token is invalid.' });
      return null;
    }
    if (!allowInactive && attempt.status !== 'active') {
      res.status(409).json({
        error: attempt.status === 'cancelled' ? 'attempt_cancelled' : 'invalid_lease',
        message: `The worker attempt is ${attempt.status}.`,
      });
      return null;
    }
    if (!allowInactive && attempt.lease_expires_at < Date.now()) {
      expireAttempts(Date.now());
      res.status(409).json({ error: 'invalid_lease', message: 'The worker lease expired.' });
      return null;
    }
    return { auth, job, attempt };
  }

  app.post(['/worker/attempts/:id/status', '/api/worker/attempts/:id/status'], (req, res) => {
    const auth = authenticateHost(db, 'worker:attempt-status', req.body, { consume: true });
    if (!auth.ok) return sendHostAuthError(res, auth);
    const attempt = db.prepare(`
      SELECT id, job_id, device_id, status, lease_token_hash, lease_expires_at
      FROM job_attempts WHERE id = ?
    `).get(req.params.id);
    if (!attempt || attempt.device_id !== auth.deviceId
        || !tokenHashMatches(req.body.lease_token, attempt.lease_token_hash)) {
      return res.status(404).json({ error: 'attempt_not_found' });
    }
    if (attempt.status === 'active' && attempt.lease_expires_at < Date.now()) expireAttempts(Date.now());
    const current = db.prepare('SELECT status, lease_expires_at FROM job_attempts WHERE id = ?').get(attempt.id);
    return res.json({
      ok: true,
      attempt_id: attempt.id,
      status: current.status,
      cancel_requested: current.status === 'cancelled',
      lease_expires_at: new Date(current.lease_expires_at).toISOString(),
    });
  });

  const completeAttempt = db.transaction((lease, output, metrics, now) => {
    const { job, attempt } = lease;
    const hash = outputHash(output);
    const metricsJson = metrics ? JSON.stringify(metrics) : null;
    db.prepare(`
      UPDATE job_attempts
      SET status = 'completed', output_encrypted = ?, output_hash = ?,
          metrics_json = ?, completed_at = ?
      WHERE id = ? AND status = 'active'
    `).run(
      encryptedStore.encrypt(output, `job:${job.id}:attempt:${attempt.id}:output`),
      hash,
      metricsJson,
      now,
      attempt.id,
    );

    if (job.job_kind === 'challenge') {
      const passed = hash === job.expected_output_hash;
      db.prepare(`
        UPDATE jobs SET status = ?, error = ?, comparison_status = ?,
          winning_attempt_id = ?, lease_device_id = NULL, lease_token_hash = NULL,
          lease_expires_at = NULL, updated_at = ?, completed_at = ?
        WHERE id = ?
      `).run(
        passed ? 'completed' : 'failed',
        passed ? null : 'Deterministic challenge output did not match the committed expectation.',
        passed ? 'matched' : 'conflict',
        passed ? attempt.id : null,
        now,
        now,
        job.id,
      );
      recordReputationEvent(db, {
        deviceId: attempt.device_id,
        manifestId: job.manifest_id,
        jobId: job.id,
        attemptId: attempt.id,
        eventType: passed ? 'challenge_pass' : 'challenge_fail',
        reason: passed ? 'Deterministic challenge matched.' : 'Deterministic challenge mismatch.',
        latencyMs: metrics?.duration_ms,
        now,
      });
      return { status: passed ? 'completed' : 'failed', challenge: passed ? 'passed' : 'failed', cancelled: [] };
    }

    const completed = db.prepare(`
      SELECT * FROM job_attempts
      WHERE job_id = ? AND status = 'completed'
      ORDER BY completed_at ASC, id ASC
    `).all(job.id);
    if ((job.required_results || 1) === 2 && completed.length < 2) {
      db.prepare(`
        UPDATE jobs SET status = 'leased', lease_device_id = NULL,
          lease_token_hash = NULL, lease_expires_at = NULL,
          comparison_status = 'pending', updated_at = ? WHERE id = ?
      `).run(now, job.id);
      return { status: 'verifying', challenge: null, cancelled: [] };
    }

    if ((job.required_results || 1) === 2 && completed[0].output_hash !== completed[1].output_hash) {
      db.prepare(`
        UPDATE jobs SET status = 'failed', error = ?, comparison_status = 'conflict',
          lease_device_id = NULL, lease_token_hash = NULL, lease_expires_at = NULL,
          updated_at = ?, completed_at = ? WHERE id = ?
      `).run('Independent worker results conflicted; both workers were quarantined for review.', now, now, job.id);
      const conflicted = completed.slice(0, 2);
      for (const item of conflicted) {
        recordReputationEvent(db, {
          deviceId: item.device_id,
          manifestId: job.manifest_id,
          jobId: job.id,
          attemptId: item.id,
          eventType: 'conflict',
          reason: 'Duplicate output hash disagreed.',
          now,
        });
      }
      db.prepare(`
        UPDATE job_attempts SET status = 'cancelled', completed_at = ?
        WHERE job_id = ? AND status = 'active'
      `).run(now, job.id);
      return { status: 'failed', challenge: null, conflict: true, cancelled: [] };
    }

    const winner = completed[0];
    const result = encryptedStore.decrypt(
      winner.output_encrypted,
      `job:${job.id}:attempt:${winner.id}:output`,
    );
    const cancelled = db.prepare(`
      SELECT id FROM job_attempts WHERE job_id = ? AND status = 'active'
    `).all(job.id).map((item) => item.id);
    db.prepare(`
      UPDATE job_attempts SET status = 'cancelled', completed_at = ?
      WHERE job_id = ? AND status = 'active'
    `).run(now, job.id);
    db.prepare(`
      UPDATE jobs
      SET status = 'completed', result_encrypted = ?, metrics_json = ?, error = NULL,
          winning_attempt_id = ?, comparison_status = ?, lease_device_id = NULL,
          lease_token_hash = NULL, lease_expires_at = NULL, updated_at = ?, completed_at = ?
      WHERE id = ?
    `).run(
      encryptedStore.encrypt(result, `job:${job.id}:result`),
      winner.metrics_json,
      winner.id,
      (job.required_results || 1) === 2 ? 'matched' : 'not_requested',
      now,
      now,
      job.id,
    );
    for (const item of (job.required_results || 1) === 2 ? completed.slice(0, 2) : [winner]) {
      const itemMetrics = item.metrics_json ? JSON.parse(item.metrics_json) : null;
      recordReputationEvent(db, {
        deviceId: item.device_id,
        manifestId: job.manifest_id,
        jobId: job.id,
        attemptId: item.id,
        eventType: 'success',
        reason: item.id === winner.id ? 'Accepted inference result.' : 'Matching duplicate inference result.',
        latencyMs: itemMetrics?.duration_ms,
        now,
      });
    }
    return { status: 'completed', challenge: null, cancelled };
  });

  app.post(['/worker/jobs/:id/complete', '/api/worker/jobs/:id/complete'], (req, res) => {
    if (!ensureEncryption(res)) return undefined;
    const lease = loadWorkerAttempt(req, res, 'worker:complete');
    if (!lease) return undefined;
    const output = typeof req.body.output === 'string' ? req.body.output.trim() : '';
    if (!output || output.length > MAX_RESULT_CHARACTERS) {
      return res.status(400).json({
        error: 'invalid_result',
        message: `output must contain 1–${MAX_RESULT_CHARACTERS} characters.`,
      });
    }
    const result = completeAttempt(lease, output, normalizeMetrics(req.body.metrics), Date.now());
    return res.json({
      ok: true,
      id: lease.job.id,
      attempt_id: lease.attempt.id,
      status: result.status,
      challenge: result.challenge,
      conflict: Boolean(result.conflict),
      cancelled_attempt_ids: result.cancelled,
    });
  });

  const failAttempt = db.transaction((lease, error, now) => {
    db.prepare(`
      UPDATE job_attempts SET status = 'failed', error = ?, completed_at = ?
      WHERE id = ? AND status = 'active'
    `).run(error, now, lease.attempt.id);
    recordReputationEvent(db, {
      deviceId: lease.attempt.device_id,
      manifestId: lease.job.manifest_id,
      jobId: lease.job.id,
      attemptId: lease.attempt.id,
      eventType: 'failure',
      reason: error,
      now,
    });
    const active = db.prepare(`
      SELECT COUNT(*) AS count FROM job_attempts WHERE job_id = ? AND status = 'active'
    `).get(lease.job.id).count;
    const terminal = lease.job.attempts >= lease.job.max_attempts && active === 0;
    const status = active > 0 ? 'leased' : terminal ? 'failed' : 'queued';
    db.prepare(`
      UPDATE jobs
      SET status = ?, error = ?, lease_device_id = NULL, lease_token_hash = NULL,
          lease_expires_at = NULL, updated_at = ?
      WHERE id = ?
    `).run(status, error, now, lease.job.id);
    return { status, retrying: !terminal };
  });

  app.post(['/worker/jobs/:id/fail', '/api/worker/jobs/:id/fail'], (req, res) => {
    if (!ensureEncryption(res)) return undefined;
    const lease = loadWorkerAttempt(req, res, 'worker:fail');
    if (!lease) return undefined;
    const result = failAttempt(lease, cleanError(req.body.error), Date.now());
    return res.json({ ok: true, id: lease.job.id, attempt_id: lease.attempt.id, ...result });
  });

  app.get('/admin/reliability/conflicts', requireAdmin, (_req, res) => {
    const rows = db.prepare(`
      SELECT j.id, j.manifest_id, j.error, j.created_at, j.updated_at,
             GROUP_CONCAT(ja.device_id || ':' || ja.output_hash) AS worker_hashes
      FROM jobs j JOIN job_attempts ja ON ja.job_id = j.id
      WHERE j.comparison_status = 'conflict' AND ja.output_hash IS NOT NULL
      GROUP BY j.id ORDER BY j.updated_at DESC
    `).all();
    return res.json({
      ok: true,
      plaintext_included: false,
      conflicts: rows.map((row) => ({
        ...row,
        worker_hashes: String(row.worker_hashes).split(','),
        created_at: new Date(row.created_at).toISOString(),
        updated_at: new Date(row.updated_at).toISOString(),
      })),
    });
  });
}

module.exports = {
  installJobRoutes,
  normalizeMetrics,
  normalizedOutput,
  outputHash,
  publicJob,
};
