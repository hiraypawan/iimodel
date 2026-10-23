'use strict';

const { requireConfiguredBearer } = require('./security');

const SCORE_HALF_LIFE_MS = 7 * 24 * 60 * 60 * 1_000;
const CONFLICT_QUARANTINE_MS = 15 * 60 * 1_000;
const CHALLENGE_QUARANTINE_MS = 24 * 60 * 60 * 1_000;
const MAX_BENCHMARK_JSON_BYTES = 2_000;

function boundedInteger(value, minimum, maximum, fallback) {
  const number = Number(value);
  return Number.isInteger(number) && number >= minimum && number <= maximum
    ? number
    : fallback;
}

function boundedNumber(value, minimum, maximum) {
  const number = Number(value);
  return Number.isFinite(number) && number >= minimum && number <= maximum
    ? Math.round(number * 1000) / 1000
    : null;
}

function cleanString(value, maximum, fallback = null) {
  if (typeof value !== 'string') return fallback;
  const clean = value.trim().replace(/[\u0000-\u001f\u007f]/g, '').slice(0, maximum);
  return clean || fallback;
}

function normalizeRegion(value, country = null) {
  const candidate = cleanString(value, 24, country);
  if (!candidate) return null;
  const normalized = candidate.toUpperCase();
  return /^[A-Z]{2}(?:-[A-Z0-9]{1,16})?$/.test(normalized) ? normalized : null;
}

function normalizeCapabilities(value, country = null) {
  const supplied = value && typeof value === 'object' && !Array.isArray(value) ? value : {};
  const runtimes = Array.isArray(supplied.supported_runtimes)
    ? [...new Set(supplied.supported_runtimes
      .map((item) => cleanString(item, 80))
      .filter(Boolean))].slice(0, 16)
    : [];
  const benchmarkInput = supplied.benchmark && typeof supplied.benchmark === 'object'
    ? supplied.benchmark
    : {};
  const benchmark = {
    schema: 'ii.worker-benchmark.v1',
    tokens_per_second: boundedNumber(benchmarkInput.tokens_per_second, 0, 1_000_000),
    prompt_tokens_per_second: boundedNumber(benchmarkInput.prompt_tokens_per_second, 0, 1_000_000),
    time_to_first_token_ms: boundedNumber(benchmarkInput.time_to_first_token_ms, 0, 3_600_000),
    sample_count: boundedInteger(benchmarkInput.sample_count, 0, 1_000_000, 0),
    measured_at: cleanString(benchmarkInput.measured_at, 40),
    manifest_id: /^[a-f0-9]{64}$/.test(String(benchmarkInput.manifest_id || ''))
      ? benchmarkInput.manifest_id
      : null,
  };
  const benchmarkJson = JSON.stringify(benchmark);
  return {
    region: normalizeRegion(supplied.region, country),
    architecture: cleanString(supplied.architecture, 40, 'unknown'),
    cpuCores: boundedInteger(supplied.cpu_cores, 1, 1_024, 1),
    memoryMb: boundedInteger(supplied.memory_mb, 0, 16_777_216, 0),
    gpuBackend: cleanString(supplied.gpu_backend, 40, 'none'),
    gpuName: cleanString(supplied.gpu_name, 120),
    vramMb: boundedInteger(supplied.vram_mb, 0, 16_777_216, 0),
    maxConcurrency: boundedInteger(supplied.max_concurrency, 1, 8, 1),
    queueDepth: boundedInteger(supplied.queue_depth, 0, 10_000, 0),
    supportedRuntimesJson: JSON.stringify(runtimes),
    benchmarkJson: Buffer.byteLength(benchmarkJson) <= MAX_BENCHMARK_JSON_BYTES
      ? benchmarkJson
      : null,
  };
}

function upsertWorkerCapabilities(db, deviceId, capabilities, now = Date.now()) {
  db.prepare(`
    INSERT INTO worker_capabilities (
      device_id, region, architecture, cpu_cores, memory_mb, gpu_backend,
      gpu_name, vram_mb, max_concurrency, queue_depth,
      supported_runtimes_json, benchmark_json, updated_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(device_id) DO UPDATE SET
      region = excluded.region,
      architecture = excluded.architecture,
      cpu_cores = excluded.cpu_cores,
      memory_mb = excluded.memory_mb,
      gpu_backend = excluded.gpu_backend,
      gpu_name = excluded.gpu_name,
      vram_mb = excluded.vram_mb,
      max_concurrency = excluded.max_concurrency,
      queue_depth = excluded.queue_depth,
      supported_runtimes_json = excluded.supported_runtimes_json,
      benchmark_json = excluded.benchmark_json,
      updated_at = excluded.updated_at
  `).run(
    deviceId,
    capabilities.region,
    capabilities.architecture,
    capabilities.cpuCores,
    capabilities.memoryMb,
    capabilities.gpuBackend,
    capabilities.gpuName,
    capabilities.vramMb,
    capabilities.maxConcurrency,
    capabilities.queueDepth,
    capabilities.supportedRuntimesJson,
    capabilities.benchmarkJson,
    now,
  );
}

function decayedScore(score, lastEventAt, now = Date.now()) {
  const elapsed = Math.max(0, now - Number(lastEventAt || now));
  const retained = 0.5 ** (elapsed / SCORE_HALF_LIFE_MS);
  return 0.5 + ((Number(score) - 0.5) * retained);
}

function scoreDelta(eventType) {
  return ({
    success: 0.015,
    failure: -0.05,
    expired: -0.07,
    challenge_pass: 0.08,
    challenge_fail: -0.3,
    conflict: -0.2,
    p2p_success: 0.01,
    p2p_failure: -0.04,
    p2p_corruption: -0.25,
  })[eventType] ?? 0;
}

function recordReputationEvent(db, {
  deviceId,
  manifestId = null,
  jobId = null,
  attemptId = null,
  source = 'scheduler',
  eventType,
  reason = null,
  latencyMs = null,
  now = Date.now(),
}) {
  const current = db.prepare(`
    SELECT * FROM worker_manifest_scores WHERE device_id = ? AND manifest_id = ?
  `).get(deviceId, manifestId);
  const previous = current ? decayedScore(current.score, current.last_event_at, now) : 0.5;
  const delta = scoreDelta(eventType);
  const nextScore = Math.max(0, Math.min(1, previous + delta));
  const increments = {
    successes: eventType === 'success' ? 1 : 0,
    failures: ['failure', 'expired'].includes(eventType) ? 1 : 0,
    challengePasses: eventType === 'challenge_pass' ? 1 : 0,
    challengeFailures: eventType === 'challenge_fail' ? 1 : 0,
    conflicts: eventType === 'conflict' ? 1 : 0,
  };
  const nextChallengeFailures = (current?.challenge_failures || 0) + increments.challengeFailures;
  let quarantineUntil = current?.quarantine_until || null;
  let quarantineReason = current?.quarantine_reason || null;
  if (eventType === 'conflict') {
    quarantineUntil = Math.max(quarantineUntil || 0, now + CONFLICT_QUARANTINE_MS);
    quarantineReason = 'Conflicting duplicate inference result pending review.';
  } else if (eventType === 'challenge_fail' && nextChallengeFailures >= 2) {
    quarantineUntil = Math.max(quarantineUntil || 0, now + CHALLENGE_QUARANTINE_MS);
    quarantineReason = 'Repeated deterministic challenge failures.';
  }
  const normalizedLatency = boundedNumber(latencyMs, 0, 3_600_000);
  const ewmaLatency = normalizedLatency === null
    ? (current?.ewma_latency_ms ?? null)
    : current?.ewma_latency_ms === null || current?.ewma_latency_ms === undefined
      ? normalizedLatency
      : Math.round(((current.ewma_latency_ms * 0.8) + (normalizedLatency * 0.2)) * 100) / 100;

  db.prepare(`
    INSERT INTO worker_manifest_scores (
      device_id, manifest_id, successes, failures, challenge_passes,
      challenge_failures, conflicts, ewma_latency_ms, score, last_event_at,
      quarantine_until, quarantine_reason
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(device_id, manifest_id) DO UPDATE SET
      successes = worker_manifest_scores.successes + excluded.successes,
      failures = worker_manifest_scores.failures + excluded.failures,
      challenge_passes = worker_manifest_scores.challenge_passes + excluded.challenge_passes,
      challenge_failures = worker_manifest_scores.challenge_failures + excluded.challenge_failures,
      conflicts = worker_manifest_scores.conflicts + excluded.conflicts,
      ewma_latency_ms = excluded.ewma_latency_ms,
      score = excluded.score,
      last_event_at = excluded.last_event_at,
      quarantine_until = excluded.quarantine_until,
      quarantine_reason = excluded.quarantine_reason
  `).run(
    deviceId,
    manifestId,
    increments.successes,
    increments.failures,
    increments.challengePasses,
    increments.challengeFailures,
    increments.conflicts,
    ewmaLatency,
    nextScore,
    now,
    quarantineUntil,
    quarantineReason,
  );
  db.prepare(`
    INSERT INTO reputation_events (
      device_id, manifest_id, job_id, attempt_id, source,
      event_type, delta, reason, created_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
  `).run(deviceId, manifestId, jobId, attemptId, source, eventType, delta, reason, now);
  return { score: nextScore, quarantineUntil, quarantineReason };
}

function publicScore(row, now = Date.now()) {
  const score = decayedScore(row.score, row.last_event_at, now);
  return {
    device_id: row.device_id,
    manifest_id: row.manifest_id,
    score: Math.round(score * 10_000) / 10_000,
    successes: row.successes,
    failures: row.failures,
    challenge_passes: row.challenge_passes,
    challenge_failures: row.challenge_failures,
    conflicts: row.conflicts,
    ewma_latency_ms: row.ewma_latency_ms,
    quarantined: Boolean(row.quarantine_until && row.quarantine_until > now),
    quarantine_until: row.quarantine_until ? new Date(row.quarantine_until).toISOString() : null,
    quarantine_reason: row.quarantine_reason,
    last_event_at: new Date(row.last_event_at).toISOString(),
  };
}

function installReliabilityRoutes(app, db) {
  const requireAdmin = requireConfiguredBearer('II_ADMIN_API_TOKEN', 'admin_api_not_configured');

  app.get(['/reliability/replicas', '/api/reliability/replicas'], (_req, res) => {
    const rows = db.prepare(`
      SELECT rt.manifest_id, rt.artifact_id, rt.target_replicas, rt.min_regions,
             COUNT(DISTINCT CASE
               WHEN d.status = 'online' AND d.last_heartbeat >= ? THEN ha.device_id
             END) AS live_replicas,
             COUNT(DISTINCT CASE
               WHEN d.status = 'online' AND d.last_heartbeat >= ?
                 THEN COALESCE(wc.region, d.country)
             END) AS live_regions
      FROM replica_targets rt
      LEFT JOIN host_artifacts ha
        ON ha.manifest_id = rt.manifest_id AND ha.artifact_id = rt.artifact_id
      LEFT JOIN devices d ON d.id = ha.device_id
      LEFT JOIN worker_capabilities wc ON wc.device_id = d.id
      GROUP BY rt.manifest_id, rt.artifact_id
      ORDER BY rt.manifest_id, rt.artifact_id
    `).all(Date.now() - 60_000, Date.now() - 60_000);
    return res.json({
      ok: true,
      source: 'signed_live_artifact_claims_and_declared_regions',
      targets: rows.map((row) => ({
        ...row,
        replica_deficit: Math.max(0, row.target_replicas - row.live_replicas),
        region_deficit: Math.max(0, row.min_regions - row.live_regions),
      })),
    });
  });

  app.put('/admin/reliability/replicas/:manifestId/:artifactId', requireAdmin, (req, res) => {
    const manifestId = req.params.manifestId;
    const artifactId = cleanString(req.params.artifactId, 120);
    const targetReplicas = boundedInteger(req.body?.target_replicas, 1, 1_000, null);
    const minRegions = boundedInteger(req.body?.min_regions, 1, 100, null);
    const artifact = db.prepare(`
      SELECT manifest_id FROM host_artifacts WHERE manifest_id = ? AND artifact_id = ?
      UNION SELECT manifest_id FROM model_manifests
      WHERE manifest_id = ? AND instr(manifest_json, ?) > 0
      LIMIT 1
    `).get(manifestId, artifactId, manifestId, `\"id\":\"${artifactId}\"`);
    if (!artifact || !targetReplicas || !minRegions) {
      return res.status(400).json({
        error: 'invalid_replica_target',
        message: 'Use a signed manifest artifact and bounded target_replicas/min_regions values.',
      });
    }
    db.prepare(`
      INSERT INTO replica_targets (
        manifest_id, artifact_id, target_replicas, min_regions, updated_at
      ) VALUES (?, ?, ?, ?, ?)
      ON CONFLICT(manifest_id, artifact_id) DO UPDATE SET
        target_replicas = excluded.target_replicas,
        min_regions = excluded.min_regions,
        updated_at = excluded.updated_at
    `).run(manifestId, artifactId, targetReplicas, minRegions, Date.now());
    return res.json({ ok: true, manifest_id: manifestId, artifact_id: artifactId });
  });

  app.get('/admin/reliability/workers', requireAdmin, (_req, res) => {
    const now = Date.now();
    const rows = db.prepare(`
      SELECT wms.*, wc.region, wc.architecture, wc.cpu_cores, wc.memory_mb,
             wc.gpu_backend, wc.gpu_name, wc.vram_mb, wc.max_concurrency,
             wc.queue_depth, wc.benchmark_json
      FROM worker_manifest_scores wms
      LEFT JOIN worker_capabilities wc ON wc.device_id = wms.device_id
      ORDER BY wms.score DESC, wms.last_event_at DESC
    `).all();
    return res.json({
      ok: true,
      attestation: 'self_declared_capabilities_only',
      workers: rows.map((row) => ({
        ...publicScore(row, now),
        capabilities: {
          region: row.region,
          architecture: row.architecture,
          cpu_cores: row.cpu_cores,
          memory_mb: row.memory_mb,
          gpu_backend: row.gpu_backend,
          gpu_name: row.gpu_name,
          vram_mb: row.vram_mb,
          max_concurrency: row.max_concurrency,
          queue_depth: row.queue_depth,
          benchmark: row.benchmark_json ? JSON.parse(row.benchmark_json) : null,
        },
      })),
    });
  });

  app.post('/admin/reliability/workers/:deviceId/:manifestId/release', requireAdmin, (req, res) => {
    const result = db.prepare(`
      UPDATE worker_manifest_scores
      SET quarantine_until = NULL, quarantine_reason = NULL, last_event_at = ?
      WHERE device_id = ? AND manifest_id = ?
    `).run(Date.now(), req.params.deviceId, req.params.manifestId);
    if (!result.changes) return res.status(404).json({ error: 'score_not_found' });
    return res.json({ ok: true, released: true });
  });
}

module.exports = {
  CONFLICT_QUARANTINE_MS,
  SCORE_HALF_LIFE_MS,
  decayedScore,
  installReliabilityRoutes,
  normalizeCapabilities,
  normalizeRegion,
  publicScore,
  recordReputationEvent,
  scoreDelta,
  upsertWorkerCapabilities,
};
