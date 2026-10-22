'use strict';

const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const express = require('express');
const Database = require('better-sqlite3');
const { installJobRoutes } = require('./jobs');
const { installLearningRoutes } = require('./learning');
const { installModelRoutes, validateArtifactClaim } = require('./model-registry');
const {
  authenticateHost,
  parseEncryptionKey,
  requireConfiguredBearer,
  secureTokenMatch,
  sendHostAuthError,
} = require('./security');

const PORT = Number.parseInt(process.env.PORT || '8787', 10);
const HOST = process.env.HOST || '0.0.0.0';
const HEARTBEAT_INTERVAL_MS = 30_000;
const ONLINE_WINDOW_MS = 60_000;
const REGISTER_RATE_LIMIT = 10;
const REGISTER_RATE_WINDOW_MS = 60_000;
const EXPERT_COUNT = 256;
const EXPERTS_PER_DEVICE = 13;
const HOST_TARGET_GB = 20;
const GENESIS_LIMIT = 400;

const configuredDbPath = process.env.II_DB_PATH || path.join(__dirname, 'data', 'ii-network.db');
if (configuredDbPath !== ':memory:') {
  fs.mkdirSync(path.dirname(configuredDbPath), { recursive: true });
}

const db = new Database(configuredDbPath);
db.pragma('foreign_keys = ON');
db.pragma('busy_timeout = 5000');
db.exec(fs.readFileSync(path.join(__dirname, 'schema.sql'), 'utf8'));

function ensureColumn(table, column, definition) {
  const columns = new Set(
    db.prepare(`PRAGMA table_info(${table})`).all().map((item) => item.name),
  );
  if (!columns.has(column)) {
    db.exec(`ALTER TABLE ${table} ADD COLUMN ${column} ${definition}`);
  }
}

// Forward-only compatibility for databases created by the PR #6 MVP.
ensureColumn('devices', 'country', 'TEXT');
ensureColumn('devices', 'experts_start', 'INTEGER');
ensureColumn('devices', 'experts_end', 'INTEGER');
ensureColumn('devices', 'last_heartbeat', 'INTEGER NOT NULL DEFAULT 0');
ensureColumn('devices', 'public_key', 'TEXT');
ensureColumn('devices', 'last_signed_at', 'INTEGER NOT NULL DEFAULT 0');
db.exec('CREATE INDEX IF NOT EXISTS idx_devices_last_heartbeat ON devices(last_heartbeat DESC)');

// v0.2 mixed desired assignments with claims of files actually hosted. Preserve
// those rows as placement advice, but do not count an unverifiable legacy claim
// as executable capacity.
const artifactTruthMigration = db
  .prepare("SELECT value FROM network_meta WHERE key = 'artifact_truth_v1'")
  .get();
if (!artifactTruthMigration) {
  db.exec(`
    INSERT OR IGNORE INTO device_assignments (device_id, expert_id)
    SELECT device_id, expert_id FROM device_experts;
    DELETE FROM device_experts;
    INSERT INTO network_meta (key, value) VALUES ('artifact_truth_v1', 'complete');
  `);
}

const legacyGenesisExists = db.prepare(`
  SELECT 1 AS found FROM sqlite_master
  WHERE type = 'table' AND name = 'genesis_applications'
`).get();
if (legacyGenesisExists) {
  db.exec(`
    INSERT OR IGNORE INTO genesis (email, country, created_at)
    SELECT email, country, created_at FROM genesis_applications
  `);
}

const app = express();
app.disable('x-powered-by');
if (process.env.TRUST_PROXY) {
  const trustProxy = process.env.TRUST_PROXY;
  app.set('trust proxy', trustProxy === 'true'
    ? true
    : /^\d+$/.test(trustProxy) ? Number.parseInt(trustProxy, 10) : trustProxy);
}
app.use(express.json({ limit: '128kb' }));

const allowedOrigins = (process.env.ALLOWED_ORIGINS || '*')
  .split(',')
  .map((origin) => origin.trim())
  .filter(Boolean);

app.use((req, res, next) => {
  const origin = req.get('origin');
  if (allowedOrigins.includes('*')) {
    res.set('Access-Control-Allow-Origin', '*');
  } else if (origin && allowedOrigins.includes(origin)) {
    res.set('Access-Control-Allow-Origin', origin);
    res.set('Vary', 'Origin');
  }

  res.set('Access-Control-Allow-Headers', 'Authorization, Content-Type');
  res.set('Access-Control-Allow-Methods', 'GET,POST,DELETE,OPTIONS');
  res.set('Cache-Control', 'no-store');

  if (req.method === 'OPTIONS') {
    return res.sendStatus(204);
  }
  return next();
});

const registrationAttempts = new Map();
let rateLimitRequests = 0;

function registerRateLimit(req, res, next) {
  const now = Date.now();
  const key = req.ip || req.socket.remoteAddress || 'unknown';
  const recent = (registrationAttempts.get(key) || [])
    .filter((timestamp) => timestamp > now - REGISTER_RATE_WINDOW_MS);

  res.set('RateLimit-Limit', String(REGISTER_RATE_LIMIT));
  res.set('RateLimit-Remaining', String(Math.max(0, REGISTER_RATE_LIMIT - recent.length - 1)));
  res.set('RateLimit-Reset', String(Math.ceil((now + REGISTER_RATE_WINDOW_MS) / 1000)));

  if (recent.length >= REGISTER_RATE_LIMIT) {
    const retryAfterSeconds = Math.max(
      1,
      Math.ceil((recent[0] + REGISTER_RATE_WINDOW_MS - now) / 1000),
    );
    res.set('Retry-After', String(retryAfterSeconds));
    return res.status(429).json({
      error: 'registration_rate_limited',
      message: 'At most 10 device registrations are allowed per IP each minute.',
      retryAfterSeconds,
    });
  }

  recent.push(now);
  registrationAttempts.set(key, recent);

  rateLimitRequests += 1;
  if (rateLimitRequests % 100 === 0) {
    for (const [address, attempts] of registrationAttempts) {
      if (!attempts.some((timestamp) => timestamp > now - REGISTER_RATE_WINDOW_MS)) {
        registrationAttempts.delete(address);
      }
    }
  }

  return next();
}

function cleanText(value, maxLength, fallback = '') {
  if (typeof value !== 'string') return fallback;
  const cleaned = value.trim().replace(/[\u0000-\u001f\u007f]/g, '');
  return cleaned.slice(0, maxLength) || fallback;
}

function isValidDeviceId(value) {
  return typeof value === 'string' && /^[a-zA-Z0-9._:-]{3,128}$/.test(value);
}

function parseOptionalNumber(value, min, max) {
  if (value === null || value === undefined || value === '') return null;
  const number = Number(value);
  if (!Number.isFinite(number) || number < min || number > max) return null;
  return Math.round(number * 100) / 100;
}

function normalizeCountry(value) {
  const country = cleanText(value, 2).toUpperCase();
  return /^[A-Z]{2}$/.test(country) ? country : null;
}

function normalizeInferenceEndpoint(value) {
  const candidate = cleanText(value, 300, null);
  if (!candidate) return null;
  try {
    const parsed = new URL(candidate);
    const localHttp = parsed.protocol === 'http:'
      && ['127.0.0.1', 'localhost', '[::1]'].includes(parsed.hostname);
    if (parsed.protocol !== 'https:' && !localHttp) return null;
    if (parsed.username || parsed.password) return null;
    parsed.hash = '';
    return parsed.toString();
  } catch {
    return null;
  }
}

function assignedExperts(genesisNumber) {
  const start = ((genesisNumber - 1) * EXPERTS_PER_DEVICE) % EXPERT_COUNT;
  return Array.from(
    { length: EXPERTS_PER_DEVICE },
    (_, index) => (start + index) % EXPERT_COUNT,
  );
}

function expertsForDevice(device) {
  const expertIds = db
    .prepare('SELECT expert_id FROM device_assignments WHERE device_id = ? ORDER BY expert_id')
    .all(device.id)
    .map((row) => row.expert_id);
  if (!expertIds.length) return [];

  let start = device.experts_start;
  if (!Number.isInteger(start)) {
    let largestGap = -1;
    let startIndex = 0;
    for (let index = 0; index < expertIds.length; index += 1) {
      const nextIndex = (index + 1) % expertIds.length;
      const gap = (expertIds[nextIndex] - expertIds[index] + EXPERT_COUNT) % EXPERT_COUNT;
      if (gap > largestGap) {
        largestGap = gap;
        startIndex = nextIndex;
      }
    }
    start = expertIds[startIndex];
  }

  return expertIds.sort((left, right) => (
    ((left - start + EXPERT_COUNT) % EXPERT_COUNT)
    - ((right - start + EXPERT_COUNT) % EXPERT_COUNT)
  ));
}

function expertLabel(experts) {
  if (!experts.length) return 'None';
  const first = experts[0];
  const last = experts[experts.length - 1];
  if (last >= first) return `${first}–${last}`;
  return `${first}–255, 0–${last}`;
}

const insertDevice = db.prepare(`
  INSERT INTO devices (
    id, device_name, platform, client_version, ip_address, endpoint,
    memory_gb, shard_size_gb, genesis_number, status, latency_ms,
    country, experts_start, experts_end, public_key, last_signed_at,
    registered_at, last_seen, last_heartbeat
  ) VALUES (
    @id, @deviceName, @platform, @clientVersion, @ipAddress, @endpoint,
    @memoryGb, @shardSizeGb, @genesisNumber, 'online', @latencyMs,
    @country, @expertsStart, @expertsEnd, @publicKey, @signedAt,
    @now, @now, @now
  )
`);

const updateDeviceRegistration = db.prepare(`
  UPDATE devices
  SET device_name = @deviceName,
      platform = @platform,
      client_version = @clientVersion,
      ip_address = @ipAddress,
      endpoint = COALESCE(@endpoint, endpoint),
      memory_gb = COALESCE(@memoryGb, memory_gb),
      shard_size_gb = COALESCE(@hostedSizeGb, shard_size_gb),
      country = COALESCE(@country, country),
      experts_start = COALESCE(@expertsStart, experts_start),
      experts_end = COALESCE(@expertsEnd, experts_end),
      status = 'online',
      latency_ms = COALESCE(@latencyMs, latency_ms),
      public_key = COALESCE(@publicKey, public_key),
      last_signed_at = CASE WHEN @signedAt > last_signed_at THEN @signedAt ELSE last_signed_at END,
      last_seen = @now,
      last_heartbeat = @now
  WHERE id = @id
`);

const deleteDeviceAssignments = db.prepare('DELETE FROM device_assignments WHERE device_id = ?');
const insertDeviceAssignment = db.prepare(
  'INSERT INTO device_assignments (device_id, expert_id) VALUES (?, ?)',
);

function replaceDeviceAssignments(deviceId, experts) {
  deleteDeviceAssignments.run(deviceId);
  for (const expertId of experts) insertDeviceAssignment.run(deviceId, expertId);
}

function validateArtifactClaims(body) {
  const supplied = body.artifacts ?? (body.artifact ? [body.artifact] : []);
  if (!Array.isArray(supplied) || supplied.length > 32) {
    return { ok: false, message: 'artifacts must be an array containing at most 32 manifest-backed claims.' };
  }
  const claims = [];
  const seen = new Set();
  for (const candidate of supplied) {
    const result = validateArtifactClaim(db, candidate);
    if (!result.ok) return result;
    const key = `${result.claim.manifest_id}:${result.claim.artifact_id}`;
    if (seen.has(key)) return { ok: false, message: 'Duplicate artifact claim.' };
    seen.add(key);
    claims.push(result.claim);
  }
  return { ok: true, claims };
}

const replaceHostedArtifacts = db.transaction((deviceId, claims, now) => {
  db.prepare('DELETE FROM host_artifacts WHERE device_id = ?').run(deviceId);
  db.prepare('DELETE FROM device_experts WHERE device_id = ?').run(deviceId);
  const insertArtifact = db.prepare(`
    INSERT INTO host_artifacts (
      device_id, manifest_id, artifact_id, sha256, size_bytes, format,
      expert_id, inference_ready, updated_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
  `);
  const insertExpert = db.prepare(
    'INSERT OR IGNORE INTO device_experts (device_id, expert_id) VALUES (?, ?)',
  );
  let totalBytes = 0;
  for (const claim of claims) {
    insertArtifact.run(
      deviceId,
      claim.manifest_id,
      claim.artifact_id,
      claim.sha256,
      claim.size_bytes,
      claim.format,
      claim.expert_id,
      claim.inference_ready ? 1 : 0,
      now,
    );
    if (Number.isInteger(claim.expert_id)) insertExpert.run(deviceId, claim.expert_id);
    totalBytes += claim.size_bytes;
  }
  db.prepare('UPDATE devices SET shard_size_gb = ? WHERE id = ?')
    .run(Math.round((totalBytes / (1024 ** 3)) * 1_000_000) / 1_000_000, deviceId);
  if (claims.length > 0) {
    const nextGenesis = db.prepare(`
      SELECT COALESCE(MAX(genesis_number), 0) + 1 AS number FROM genesis_hosts
    `).get().number;
    db.prepare(`
      INSERT OR IGNORE INTO genesis_hosts (device_id, genesis_number, verified_at)
      SELECT id, ?, ? FROM devices WHERE id = ? AND public_key IS NOT NULL
    `).run(nextGenesis, now, deviceId);
  }
});

const registerDevice = db.transaction((details) => {
  let device = db.prepare('SELECT * FROM devices WHERE id = ?').get(details.id);
  let created = false;
  let experts;

  if (!device) {
    const nextNumber = db
      .prepare('SELECT COALESCE(MAX(genesis_number), 0) + 1 AS number FROM devices')
      .get().number;
    experts = assignedExperts(nextNumber);

    insertDevice.run({
      ...details,
      genesisNumber: nextNumber,
      shardSizeGb: details.hostedSizeGb ?? 0,
      expertsStart: experts[0],
      expertsEnd: experts[experts.length - 1],
    });
    replaceDeviceAssignments(details.id, experts);
    created = true;
  } else {
    experts = expertsForDevice(device);
    if (experts.length !== EXPERTS_PER_DEVICE) {
      experts = assignedExperts(device.genesis_number);
      replaceDeviceAssignments(details.id, experts);
    }
    updateDeviceRegistration.run({
      ...details,
      expertsStart: experts[0],
      expertsEnd: experts[experts.length - 1],
    });
  }

  device = db.prepare('SELECT * FROM devices WHERE id = ?').get(details.id);
  return { device, created };
});

function registrationPayload(device) {
  const experts = expertsForDevice(device);
  const genesis = db.prepare(`
    SELECT genesis_number, verified_at FROM genesis_hosts WHERE device_id = ?
  `).get(device.id);
  return {
    deviceId: device.id,
    device_id: device.id,
    status: device.status,
    hostNumber: device.genesis_number,
    genesisNumber: genesis?.genesis_number ?? null,
    genesisMember: Boolean(genesis && genesis.genesis_number <= GENESIS_LIMIT),
    genesisVerifiedAt: genesis ? new Date(genesis.verified_at).toISOString() : null,
    experts,
    expertsStart: experts[0],
    expertsEnd: experts[experts.length - 1],
    experts_start: experts[0],
    experts_end: experts[experts.length - 1],
    expertsLabel: expertLabel(experts),
    hostedSizeGb: device.shard_size_gb,
    shardSizeGb: device.shard_size_gb,
    hostTargetGb: HOST_TARGET_GB,
    heartbeatIntervalMs: HEARTBEAT_INTERVAL_MS,
    registeredAt: new Date(device.registered_at).toISOString(),
  };
}

function registerHandler(req, res) {
  const body = req.body || {};
  const deviceId = body.device_id || body.deviceId;
  if (!isValidDeviceId(deviceId)) {
    return res.status(400).json({
      error: 'invalid_device_id',
      message: 'deviceId must be 3–128 letters, numbers, dots, colons, underscores, or hyphens.',
    });
  }

  const auth = authenticateHost(db, 'host:register', body, { allowNew: true });
  if (!auth.ok) return sendHostAuthError(res, auth);

  const artifactResult = validateArtifactClaims(body);
  if (!artifactResult.ok) {
    return res.status(400).json({ error: 'invalid_artifact_claim', message: artifactResult.message });
  }

  const endpoint = normalizeInferenceEndpoint(body.endpoint);
  if (body.endpoint && !endpoint) {
    return res.status(400).json({
      error: 'invalid_endpoint',
      message: 'endpoint must use HTTPS (localhost HTTP is allowed for development).',
    });
  }

  if (endpoint && !artifactResult.claims.some((claim) => claim.inference_ready)) {
    return res.status(400).json({
      error: 'endpoint_not_ready',
      message: 'A public inference endpoint may be advertised only with a manifest-backed ready artifact.',
    });
  }

  const now = Date.now();
  const hostedSizeGb = Math.round((artifactResult.claims.reduce(
    (total, claim) => total + claim.size_bytes,
    0,
  ) / (1024 ** 3)) * 1_000_000) / 1_000_000;
  const details = {
    id: deviceId,
    deviceName: cleanText(body.deviceName, 100, `II Host ${deviceId.slice(0, 8)}`),
    platform: cleanText(body.platform, 50, 'unknown'),
    clientVersion: cleanText(body.clientVersion, 30, '0.1.0'),
    ipAddress: cleanText(req.ip, 64, null),
    endpoint,
    country: normalizeCountry(body.country),
    hostedSizeGb,
    memoryGb: parseOptionalNumber(body.memoryGb, 0, 16_384),
    latencyMs: parseOptionalNumber(body.latencyMs, 0, 60_000),
    publicKey: auth.publicKey,
    signedAt: auth.signedAt,
    now,
  };

  const registration = registerDevice(details);
  replaceHostedArtifacts(deviceId, artifactResult.claims, now);
  const device = db.prepare('SELECT * FROM devices WHERE id = ?').get(deviceId);
  return res.status(registration.created ? 201 : 200).json({
    ok: true,
    identityVerified: !auth.legacy,
    artifactClaims: artifactResult.claims.length,
    ...registrationPayload(device),
  });
}

const recordHeartbeat = db.transaction((details) => {
  replaceHostedArtifacts(details.deviceId, details.claims, details.now);
  db.prepare(`
    UPDATE devices
    SET status = ?,
        last_seen = ?,
        last_heartbeat = ?,
        latency_ms = COALESCE(?, latency_ms),
        endpoint = ?,
        ip_address = ?,
        last_signed_at = CASE WHEN ? > last_signed_at THEN ? ELSE last_signed_at END
    WHERE id = ?
  `).run(
    details.status,
    details.now,
    details.now,
    details.latencyMs,
    details.endpoint,
    details.ipAddress,
    details.signedAt,
    details.signedAt,
    details.deviceId,
  );
});

function heartbeatHandler(req, res) {
  const body = req.body || {};
  const deviceId = body.device_id || body.deviceId;
  if (!isValidDeviceId(deviceId)) {
    return res.status(400).json({
      error: 'invalid_device_id',
      message: 'A valid device_id is required.',
    });
  }

  const auth = authenticateHost(db, 'host:heartbeat', body);
  if (!auth.ok) return sendHostAuthError(res, auth);

  const artifactResult = validateArtifactClaims(body);
  if (!artifactResult.ok) {
    return res.status(400).json({ error: 'invalid_artifact_claim', message: artifactResult.message });
  }

  const status = body.status === 'offline' ? 'offline' : 'online';
  const latencyMs = parseOptionalNumber(body.latency_ms ?? body.latencyMs, 0, 60_000);
  const endpoint = normalizeInferenceEndpoint(body.endpoint);
  if (body.endpoint && !endpoint) {
    return res.status(400).json({
      error: 'invalid_endpoint',
      message: 'endpoint must use HTTPS (localhost HTTP is allowed for development).',
    });
  }
  if (endpoint && !artifactResult.claims.some((claim) => claim.inference_ready)) {
    return res.status(400).json({
      error: 'endpoint_not_ready',
      message: 'A public inference endpoint requires a manifest-backed ready artifact.',
    });
  }
  const now = Date.now();

  recordHeartbeat({
    deviceId,
    status,
    now,
    claims: artifactResult.claims,
    latencyMs,
    endpoint,
    signedAt: auth.signedAt,
    ipAddress: cleanText(req.ip, 64, null),
  });

  return res.json({
    ok: true,
    deviceId,
    device_id: deviceId,
    identityVerified: !auth.legacy,
    status,
    artifactClaims: artifactResult.claims.length,
    hostedExperts: artifactResult.claims
      .map((claim) => claim.expert_id)
      .filter(Number.isInteger),
    serverTime: new Date(now).toISOString(),
    nextHeartbeatInMs: HEARTBEAT_INTERVAL_MS,
  });
}

function networkStats() {
  const now = Date.now();
  const cutoff = now - ONLINE_WINDOW_MS;

  db.prepare(`
    UPDATE devices
    SET status = 'offline'
    WHERE status = 'online' AND last_heartbeat < ?
  `).run(cutoff);

  const totalHosts = db.prepare('SELECT COUNT(*) AS count FROM devices').get().count;
  const onlineHosts = db.prepare(`
    SELECT COUNT(DISTINCT d.id) AS count
    FROM devices d
    JOIN host_artifacts ha ON ha.device_id = d.id
    JOIN model_manifests m ON m.manifest_id = ha.manifest_id AND m.active = 1
    WHERE d.status = 'online' AND d.last_heartbeat >= ? AND d.public_key IS NOT NULL
  `).get(cutoff).count;
  const hostedCapacityBytes = db.prepare(`
    SELECT COALESCE(SUM(ha.size_bytes), 0) AS total
    FROM host_artifacts ha
    JOIN devices d ON d.id = ha.device_id
    JOIN model_manifests m ON m.manifest_id = ha.manifest_id AND m.active = 1
    WHERE d.status = 'online' AND d.last_heartbeat >= ? AND d.public_key IS NOT NULL
  `).get(cutoff).total;

  const storedCoverageRows = db.prepare(`
    SELECT ha.expert_id, COUNT(*) AS replicas
    FROM host_artifacts ha
    JOIN devices d ON d.id = ha.device_id
    JOIN model_manifests m ON m.manifest_id = ha.manifest_id AND m.active = 1
    WHERE d.status = 'online' AND d.last_heartbeat >= ?
      AND d.public_key IS NOT NULL AND ha.expert_id IS NOT NULL
    GROUP BY ha.expert_id
  `).all(cutoff);
  const routableCoverageRows = db.prepare(`
    SELECT ha.expert_id, COUNT(*) AS replicas
    FROM host_artifacts ha
    JOIN devices d ON d.id = ha.device_id
    JOIN model_manifests m ON m.manifest_id = ha.manifest_id AND m.active = 1
    WHERE d.status = 'online' AND d.last_heartbeat >= ?
      AND d.public_key IS NOT NULL AND d.endpoint IS NOT NULL
      AND ha.expert_id IS NOT NULL AND ha.inference_ready = 1
    GROUP BY ha.expert_id
  `).all(cutoff);
  const readyWorkers = db.prepare(`
    SELECT COUNT(DISTINCT d.id) AS count
    FROM host_artifacts ha
    JOIN devices d ON d.id = ha.device_id
    JOIN model_manifests m ON m.manifest_id = ha.manifest_id
    WHERE m.active = 1 AND m.role = 'worker' AND ha.inference_ready = 1
      AND d.public_key IS NOT NULL AND d.status = 'online' AND d.last_heartbeat >= ?
  `).get(cutoff).count;

  const storedCoveredExperts = storedCoverageRows.length;
  const coveredExperts = routableCoverageRows.length;
  const storedFullCopies = storedCoveredExperts === EXPERT_COUNT
    ? Math.min(...storedCoverageRows.map((row) => row.replicas))
    : 0;
  const fullCopies = coveredExperts === EXPERT_COUNT
    ? Math.min(...routableCoverageRows.map((row) => row.replicas))
    : 0;
  const applications = db.prepare('SELECT COUNT(*) AS count FROM genesis').get().count;
  const verifiedHosts = db.prepare(`
    SELECT COUNT(DISTINCT d.id) AS count
    FROM devices d JOIN host_artifacts ha ON ha.device_id = d.id
    WHERE d.public_key IS NOT NULL
  `).get().count;
  const genesisClaimed = db.prepare(`
    SELECT COUNT(*) AS count FROM genesis_hosts WHERE genesis_number <= ?
  `).get(GENESIS_LIMIT).count;
  const jobCounts = db.prepare(`
    SELECT
      SUM(CASE WHEN status = 'queued' THEN 1 ELSE 0 END) AS queued,
      SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed
    FROM jobs
  `).get();

  return {
    ok: true,
    network: 'II decentralized worker network',
    onlineHosts,
    totalHosts,
    identityVerifiedHosts: verifiedHosts,
    readyWorkers,
    storedCoveredExperts,
    coveredExperts,
    totalExperts: EXPERT_COUNT,
    coveragePercent: Math.round((coveredExperts / EXPERT_COUNT) * 10_000) / 100,
    storedFullCopies,
    fullCopies,
    coverageMeaning: 'manifest-matched, signed, live, inference-ready expert claims',
    hostedCapacityGb: Math.round((hostedCapacityBytes / (1024 ** 3)) * 100) / 100,
    hostTargetGb: HOST_TARGET_GB,
    queuedJobs: jobCounts.queued || 0,
    completedJobs: jobCounts.completed || 0,
    genesisClaimed,
    genesisRemaining: Math.max(0, GENESIS_LIMIT - genesisClaimed),
    genesisApplications: applications,
    heartbeatIntervalMs: HEARTBEAT_INTERVAL_MS,
    updatedAt: new Date(now).toISOString(),
  };
}

// TODO-REAL: Replace research-only prompt hashing with IDs produced by a measured,
// versioned router for an exact executable model topology.
function expertsForPrompt(prompt) {
  const selected = new Set();
  let round = 0;
  while (selected.size < 5) {
    const digest = crypto.createHash('sha256').update(`${round}:${prompt}`).digest();
    for (let offset = 0; offset < digest.length && selected.size < 5; offset += 2) {
      selected.add(digest.readUInt16BE(offset) % EXPERT_COUNT);
    }
    round += 1;
  }
  return [...selected];
}

function parseRequestedExperts(req) {
  const raw = req.query.expert_ids ?? req.query.experts ?? req.query.expert;
  if (raw === undefined || raw === '') {
    const prompt = cleanText(req.query.prompt, 4_000);
    return {
      experts: prompt ? expertsForPrompt(prompt) : [],
      selectionMethod: prompt ? 'deterministic_prompt_hash' : null,
    };
  }

  const pieces = Array.isArray(raw) ? raw : String(raw).split(',');
  const experts = [...new Set(
    pieces
      .map((value) => Number.parseInt(String(value).trim(), 10))
      .filter((value) => Number.isInteger(value) && value >= 0 && value < EXPERT_COUNT),
  )];

  return { experts: experts.slice(0, 64), selectionMethod: 'explicit_expert_ids' };
}

function routeHandler(req, res) {
  const { experts: requestedExperts, selectionMethod } = parseRequestedExperts(req);
  if (!requestedExperts.length) {
    return res.status(400).json({
      error: 'missing_route_input',
      message: 'Pass a prompt or expert IDs from 0–255, for example ?expert_ids=0,1.',
    });
  }

  const cutoff = Date.now() - ONLINE_WINDOW_MS;
  const placeholders = requestedExperts.map(() => '?').join(',');
  const candidates = db.prepare(`
    SELECT d.id,
           d.endpoint,
           d.country,
           d.latency_ms,
           d.last_heartbeat,
           ha.manifest_id,
           GROUP_CONCAT(ha.expert_id) AS matched_experts
    FROM devices d
    JOIN host_artifacts ha ON ha.device_id = d.id
    JOIN model_manifests m ON m.manifest_id = ha.manifest_id AND m.active = 1
    WHERE ha.expert_id IN (${placeholders})
      AND ha.inference_ready = 1
      AND d.public_key IS NOT NULL
      AND d.status = 'online'
      AND d.last_heartbeat >= ?
      AND d.endpoint IS NOT NULL
    GROUP BY d.id, ha.manifest_id
    ORDER BY CASE WHEN d.latency_ms IS NULL THEN 1 ELSE 0 END,
             d.latency_ms ASC,
             d.last_heartbeat DESC
    LIMIT 5
  `).all(...requestedExperts, cutoff);

  const devices = candidates.map((device) => ({
    deviceId: device.id,
    device_id: device.id,
    endpoint: device.endpoint,
    country: device.country,
    manifest_id: device.manifest_id,
    identityVerified: true,
    artifactVerification: 'signed_manifest_match_self_attested_runtime',
    experts: String(device.matched_experts)
      .split(',')
      .map(Number)
      .sort((left, right) => left - right),
    latencyMs: device.latency_ms,
    lastHeartbeat: new Date(device.last_heartbeat).toISOString(),
  }));

  return res.json({
    ok: true,
    requestedExperts,
    selectionMethod,
    onlineWindowSeconds: ONLINE_WINDOW_MS / 1000,
    devices,
  });
}

function genesisHandler(req, res) {
  const body = req.body || {};
  const email = cleanText(body.email, 254).toLowerCase();
  const country = cleanText(body.country, 80);
  const acceptedHostTerms = body.acceptedHostTerms === true || body.acceptedHostTerms === 'true';

  if (!/^\S+@\S+\.\S+$/.test(email) || !country) {
    return res.status(400).json({
      error: 'invalid_application',
      message: 'A valid email and country are required.',
    });
  }
  if (!acceptedHostTerms) {
    return res.status(400).json({
      error: 'host_terms_required',
      message: 'Please confirm that this computer can host the expert files.',
    });
  }

  const existing = db
    .prepare('SELECT email FROM genesis WHERE email = ?')
    .get(email);

  db.prepare(`
    INSERT INTO genesis (email, country, created_at)
    VALUES (?, ?, ?)
    ON CONFLICT(email) DO UPDATE SET country = excluded.country
  `).run(email, country, Date.now());

  return res.status(existing ? 200 : 201).json({
    ok: true,
    alreadyApplied: Boolean(existing),
    message: existing
      ? 'Your Genesis host application is already on the list.'
      : 'Application received. A place is earned only after a host registers successfully.',
  });
}

function csvCell(value) {
  let text = String(value ?? '');
  if (/^[=+\-@]/.test(text)) text = `'${text}`;
  return `"${text.replace(/"/g, '""')}"`;
}

function genesisExportHandler(req, res) {
  const configuredToken = process.env.ADMIN_EXPORT_TOKEN;
  if (!configuredToken || Buffer.byteLength(configuredToken) < 24) {
    return res.status(503).json({
      error: 'admin_export_not_configured',
      message: 'Set ADMIN_EXPORT_TOKEN to a random secret of at least 24 bytes.',
    });
  }

  const authorization = req.get('authorization') || '';
  const providedToken = authorization.startsWith('Bearer ')
    ? authorization.slice('Bearer '.length)
    : '';
  if (!secureTokenMatch(providedToken, configuredToken)) {
    res.set('WWW-Authenticate', 'Bearer');
    return res.status(401).json({
      error: 'unauthorized',
      message: 'A valid admin bearer token is required.',
    });
  }

  const rows = db
    .prepare('SELECT email, country, created_at FROM genesis ORDER BY created_at ASC')
    .all();
  const csv = [
    'email,country,created_at',
    ...rows.map((row) => [
      csvCell(row.email),
      csvCell(row.country),
      csvCell(new Date(row.created_at).toISOString()),
    ].join(',')),
  ].join('\r\n');

  const date = new Date().toISOString().slice(0, 10);
  res.set('Content-Type', 'text/csv; charset=utf-8');
  res.set('Content-Disposition', `attachment; filename="genesis-${date}.csv"`);
  return res.send(`${csv}\r\n`);
}

function statsHandler(_req, res) {
  return res.json(networkStats());
}

const revokeHost = db.transaction((device, reason, now) => {
  db.prepare(`
    INSERT INTO host_revocations (device_id, public_key, reason, revoked_at)
    VALUES (?, ?, ?, ?)
    ON CONFLICT(device_id) DO UPDATE SET reason = excluded.reason, revoked_at = excluded.revoked_at
  `).run(device.id, device.public_key, reason, now);
  db.prepare(`
    UPDATE devices SET status = 'offline', endpoint = NULL, last_seen = ? WHERE id = ?
  `).run(now, device.id);
  db.prepare('DELETE FROM host_artifacts WHERE device_id = ?').run(device.id);
  db.prepare('DELETE FROM device_experts WHERE device_id = ?').run(device.id);
  db.prepare(`
    UPDATE jobs
    SET status = CASE WHEN attempts >= max_attempts THEN 'failed' ELSE 'queued' END,
        error = CASE WHEN attempts >= max_attempts THEN 'Assigned host was revoked.' ELSE error END,
        lease_device_id = NULL, lease_token_hash = NULL, lease_expires_at = NULL,
        updated_at = ?
    WHERE status = 'leased' AND lease_device_id = ?
  `).run(now, device.id);
});

function revokeHostHandler(req, res) {
  const deviceId = req.params.deviceId;
  if (!isValidDeviceId(deviceId)) {
    return res.status(400).json({ error: 'invalid_device_id', message: 'A valid device ID is required.' });
  }
  const device = db.prepare('SELECT id, public_key FROM devices WHERE id = ?').get(deviceId);
  if (!device) return res.status(404).json({ error: 'device_not_found', message: 'Host not found.' });
  const reason = cleanText(req.body?.reason, 300);
  if (!reason) {
    return res.status(400).json({ error: 'reason_required', message: 'A bounded revocation reason is required.' });
  }
  const now = Date.now();
  revokeHost(device, reason, now);
  return res.json({
    ok: true,
    device_id: deviceId,
    revoked_at: new Date(now).toISOString(),
  });
}

app.get('/health', (_req, res) => {
  res.json({
    ok: true,
    service: 'ii-coordinator',
    version: '0.3.0',
    signedHostsRequired: process.env.II_ALLOW_UNSIGNED_HOSTS !== 'true',
    releaseTrustConfigured: Boolean(process.env.II_RELEASE_PUBLIC_KEY),
    encryptedWorkConfigured: Boolean(parseEncryptionKey()),
    time: new Date().toISOString(),
  });
});

app.post(['/register', '/api/register'], registerRateLimit, registerHandler);
app.post(['/heartbeat', '/api/heartbeat'], heartbeatHandler);
app.get(['/stats', '/api/stats'], statsHandler);
app.get(['/route', '/api/route'], routeHandler);
app.post(['/genesis', '/api/genesis'], genesisHandler);
app.get('/admin/genesis', genesisExportHandler);
app.post(
  '/admin/hosts/:deviceId/revoke',
  requireConfiguredBearer('II_ADMIN_API_TOKEN', 'admin_api_not_configured'),
  revokeHostHandler,
);
installModelRoutes(app, db);
installJobRoutes(app, db);
installLearningRoutes(app, db);

app.get(['/checkout/:plan', '/api/checkout/:plan'], async (req, res) => {
  const plan = req.params.plan;
  if (!['monthly', 'yearly'].includes(plan)) {
    return res.status(404).json({ error: 'unknown_plan', message: 'Choose monthly or yearly.' });
  }

  // A pre-created Dodo payment link is the simplest production setup.
  const directLinks = {
    monthly: process.env.DODO_MONTHLY_URL || process.env.DODO_MONTHLY_CHECKOUT_URL,
    yearly: process.env.DODO_YEARLY_URL || process.env.DODO_YEARLY_CHECKOUT_URL,
  };
  if (directLinks[plan] && /^https:\/\//i.test(directLinks[plan])) {
    return res.redirect(302, directLinks[plan]);
  }

  // Alternatively, create a fresh checkout through Dodo's API. Localized prices
  // are selected automatically because both products must be configured with
  // pricing_mode=by_country in Dodo.
  const productIds = {
    monthly: process.env.DODO_MONTHLY_PRODUCT_ID,
    yearly: process.env.DODO_YEARLY_PRODUCT_ID,
  };
  const apiKey = process.env.DODO_PAYMENTS_API_KEY;
  const productId = productIds[plan];

  if (!apiKey || !productId) {
    return res.status(503).json({
      error: 'checkout_not_configured',
      message: `Set a Dodo checkout URL or DODO_${plan.toUpperCase()}_PRODUCT_ID with DODO_PAYMENTS_API_KEY.`,
      pricingMode: 'by_country',
    });
  }

  const dodoBaseUrl = (
    process.env.DODO_PAYMENTS_BASE_URL
    || (process.env.DODO_PAYMENTS_ENVIRONMENT === 'live_mode'
      ? 'https://live.dodopayments.com'
      : 'https://test.dodopayments.com')
  ).replace(/\/$/, '');
  const publicWebUrl = (
    process.env.PUBLIC_WEB_URL
    || `${req.protocol}://${req.get('host')}`
  ).replace(/\/$/, '');

  try {
    const response = await fetch(`${dodoBaseUrl}/checkouts`, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${apiKey}`,
        Accept: 'application/json',
        'Content-Type': 'application/json',
      },
      signal: AbortSignal.timeout(15_000),
      body: JSON.stringify({
        product_cart: [{ product_id: productId, quantity: 1 }],
        return_url: `${publicWebUrl}/?checkout=success`,
        cancel_url: `${publicWebUrl}/#pricing`,
        short_link: true,
        metadata: { plan, source: 'ii-web' },
      }),
    });
    const checkout = await response.json().catch(() => ({}));

    if (!response.ok || !checkout.checkout_url) {
      console.error(
        '[coordinator] Dodo checkout failed:',
        response.status,
        cleanText(checkout?.message || checkout?.error, 200, 'unknown provider error'),
      );
      return res.status(502).json({
        error: 'checkout_provider_error',
        message: 'Dodo Payments could not create a checkout. Please try again.',
      });
    }

    let checkoutUrl;
    try {
      checkoutUrl = new URL(checkout.checkout_url);
    } catch {
      return res.status(502).json({
        error: 'invalid_checkout_url',
        message: 'Dodo Payments returned an invalid checkout URL.',
      });
    }
    if (checkoutUrl.protocol !== 'https:') {
      return res.status(502).json({
        error: 'invalid_checkout_url',
        message: 'Dodo Payments returned a non-secure checkout URL.',
      });
    }

    return res.redirect(303, checkoutUrl.toString());
  } catch (error) {
    console.error('[coordinator] Dodo API unavailable:', error.message);
    return res.status(502).json({
      error: 'checkout_provider_unavailable',
      message: 'Dodo Payments is temporarily unavailable. Please try again.',
    });
  }
});

const webDirectory = path.join(__dirname, '..', 'web');
if (fs.existsSync(webDirectory)) {
  app.use(express.static(webDirectory, { extensions: ['html'] }));
}

app.use('/api', (_req, res) => {
  res.status(404).json({ error: 'not_found', message: 'API endpoint not found.' });
});

app.use((error, _req, res, _next) => {
  if (error instanceof SyntaxError && error.status === 400 && 'body' in error) {
    return res.status(400).json({ error: 'invalid_json', message: 'Request body must be valid JSON.' });
  }
  console.error('[coordinator] request failed:', error);
  return res.status(500).json({ error: 'internal_error', message: 'Coordinator request failed.' });
});

let server;
if (require.main === module) {
  if (!Number.isInteger(PORT) || PORT < 1 || PORT > 65_535) {
    console.error('[coordinator] PORT must be an integer from 1 to 65535.');
    process.exitCode = 1;
  } else {
    server = app.listen(PORT, HOST, () => {
      console.log(`[coordinator] listening on http://${HOST}:${PORT}`);
      console.log(`[coordinator] database: ${configuredDbPath}`);
      console.log(`[coordinator] heartbeat window: ${ONLINE_WINDOW_MS / 1000} seconds`);
    });
  }
}

function shutdown(signal) {
  console.log(`\n[coordinator] ${signal} received; shutting down.`);
  const finish = () => {
    db.close();
    process.exit(0);
  };

  if (server) {
    server.close(finish);
    setTimeout(() => process.exit(1), 5_000).unref();
  } else {
    finish();
  }
}

if (require.main === module) {
  process.on('SIGINT', () => shutdown('SIGINT'));
  process.on('SIGTERM', () => shutdown('SIGTERM'));
}

module.exports = { app, db, networkStats, assignedExperts };
