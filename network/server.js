'use strict';

const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const express = require('express');
const Database = require('better-sqlite3');

const PORT = Number.parseInt(process.env.PORT || '8787', 10);
const HOST = process.env.HOST || '0.0.0.0';
const HEARTBEAT_INTERVAL_MS = 30_000;
const ONLINE_WINDOW_MS = 90_000;
const EXPERT_COUNT = 256;
const EXPERTS_PER_DEVICE = 13;
const SHARD_SIZE_GB = 20;
const GENESIS_LIMIT = 400;

const configuredDbPath = process.env.II_DB_PATH || path.join(__dirname, 'data', 'ii-network.db');
if (configuredDbPath !== ':memory:') {
  fs.mkdirSync(path.dirname(configuredDbPath), { recursive: true });
}

const db = new Database(configuredDbPath);
db.pragma('foreign_keys = ON');
db.pragma('busy_timeout = 5000');
db.exec(fs.readFileSync(path.join(__dirname, 'schema.sql'), 'utf8'));

const app = express();
app.disable('x-powered-by');
app.set('trust proxy', 1);
app.use(express.json({ limit: '32kb' }));

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

  res.set('Access-Control-Allow-Headers', 'Content-Type');
  res.set('Access-Control-Allow-Methods', 'GET,POST,OPTIONS');
  res.set('Cache-Control', 'no-store');

  if (req.method === 'OPTIONS') {
    return res.sendStatus(204);
  }
  return next();
});

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

function assignedExperts(genesisNumber) {
  const start = (12 + ((genesisNumber - 1) * EXPERTS_PER_DEVICE)) % EXPERT_COUNT;
  return Array.from(
    { length: EXPERTS_PER_DEVICE },
    (_, index) => (start + index) % EXPERT_COUNT,
  );
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
    registered_at, last_seen
  ) VALUES (
    @id, @deviceName, @platform, @clientVersion, @ipAddress, @endpoint,
    @memoryGb, @shardSizeGb, @genesisNumber, 'online', @latencyMs,
    @now, @now
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
      status = 'online',
      latency_ms = COALESCE(@latencyMs, latency_ms),
      last_seen = @now
  WHERE id = @id
`);

const registerDevice = db.transaction((details) => {
  let device = db.prepare('SELECT * FROM devices WHERE id = ?').get(details.id);
  let created = false;

  if (!device) {
    const nextNumber = db
      .prepare('SELECT COALESCE(MAX(genesis_number), 0) + 1 AS number FROM devices')
      .get().number;

    insertDevice.run({
      ...details,
      genesisNumber: nextNumber,
      shardSizeGb: SHARD_SIZE_GB,
    });

    const expertInsert = db.prepare(
      'INSERT INTO device_experts (device_id, expert_id) VALUES (?, ?)',
    );
    for (const expertId of assignedExperts(nextNumber)) {
      expertInsert.run(details.id, expertId);
    }
    created = true;
  } else {
    updateDeviceRegistration.run(details);
  }

  device = db.prepare('SELECT * FROM devices WHERE id = ?').get(details.id);
  return { device, created };
});

function registrationPayload(device) {
  const experts = assignedExperts(device.genesis_number);
  return {
    deviceId: device.id,
    status: device.status,
    genesisNumber: device.genesis_number,
    genesisMember: device.genesis_number <= GENESIS_LIMIT,
    experts,
    expertsLabel: expertLabel(experts),
    shardSizeGb: device.shard_size_gb,
    heartbeatIntervalMs: HEARTBEAT_INTERVAL_MS,
    registeredAt: new Date(device.registered_at).toISOString(),
  };
}

function registerHandler(req, res) {
  const body = req.body || {};
  const deviceId = body.deviceId || crypto.randomUUID();
  if (!isValidDeviceId(deviceId)) {
    return res.status(400).json({
      error: 'invalid_device_id',
      message: 'deviceId must be 3–128 letters, numbers, dots, colons, underscores, or hyphens.',
    });
  }

  const now = Date.now();
  const details = {
    id: deviceId,
    deviceName: cleanText(body.deviceName, 100, `II Host ${deviceId.slice(0, 8)}`),
    platform: cleanText(body.platform, 50, 'unknown'),
    clientVersion: cleanText(body.clientVersion, 30, '0.1.0'),
    ipAddress: cleanText(req.ip, 64, null),
    endpoint: cleanText(body.endpoint, 300, null),
    memoryGb: parseOptionalNumber(body.memoryGb, 0, 16_384),
    latencyMs: parseOptionalNumber(body.latencyMs, 0, 60_000),
    now,
  };

  const { device, created } = registerDevice(details);
  return res.status(created ? 201 : 200).json({
    ok: true,
    ...registrationPayload(device),
  });
}

function heartbeatHandler(req, res) {
  const body = req.body || {};
  if (!isValidDeviceId(body.deviceId)) {
    return res.status(400).json({
      error: 'invalid_device_id',
      message: 'A valid deviceId is required.',
    });
  }

  const existing = db.prepare('SELECT * FROM devices WHERE id = ?').get(body.deviceId);
  if (!existing) {
    return res.status(404).json({
      error: 'device_not_registered',
      message: 'Register this device before sending heartbeats.',
    });
  }

  const status = body.status === 'offline' ? 'offline' : 'online';
  const latencyMs = parseOptionalNumber(body.latencyMs, 0, 60_000);
  const endpoint = cleanText(body.endpoint, 300, null);
  const now = Date.now();

  db.prepare(`
    UPDATE devices
    SET status = ?,
        last_seen = ?,
        latency_ms = COALESCE(?, latency_ms),
        endpoint = COALESCE(?, endpoint),
        ip_address = ?
    WHERE id = ?
  `).run(status, now, latencyMs, endpoint, cleanText(req.ip, 64, null), body.deviceId);

  return res.json({
    ok: true,
    deviceId: body.deviceId,
    status,
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
    WHERE status = 'online' AND last_seen < ?
  `).run(cutoff);

  const totalHosts = db.prepare('SELECT COUNT(*) AS count FROM devices').get().count;
  const onlineHosts = db.prepare(`
    SELECT COUNT(*) AS count
    FROM devices
    WHERE status = 'online' AND last_seen >= ?
  `).get(cutoff).count;

  const coverageRows = db.prepare(`
    SELECT de.expert_id, COUNT(*) AS replicas
    FROM device_experts de
    JOIN devices d ON d.id = de.device_id
    WHERE d.status = 'online' AND d.last_seen >= ?
    GROUP BY de.expert_id
  `).all(cutoff);

  const coveredExperts = coverageRows.length;
  const fullCopies = coveredExperts === EXPERT_COUNT
    ? Math.min(...coverageRows.map((row) => row.replicas))
    : 0;
  const applications = db
    .prepare('SELECT COUNT(*) AS count FROM genesis_applications')
    .get().count;
  const genesisClaimed = Math.min(totalHosts, GENESIS_LIMIT);

  return {
    ok: true,
    network: 'II decentralized MVP',
    onlineHosts,
    totalHosts,
    coveredExperts,
    totalExperts: EXPERT_COUNT,
    coveragePercent: Math.round((coveredExperts / EXPERT_COUNT) * 10_000) / 100,
    fullCopies,
    hostedCapacityGb: onlineHosts * SHARD_SIZE_GB,
    genesisClaimed,
    genesisRemaining: Math.max(0, GENESIS_LIMIT - genesisClaimed),
    genesisApplications: applications,
    heartbeatIntervalMs: HEARTBEAT_INTERVAL_MS,
    updatedAt: new Date(now).toISOString(),
  };
}

function parseRequestedExperts(req) {
  const raw = req.query.experts ?? req.query.expert;
  if (raw === undefined || raw === '') {
    return [12, 13, 14, 15, 16];
  }

  const pieces = Array.isArray(raw) ? raw : String(raw).split(',');
  const experts = [...new Set(
    pieces
      .map((value) => Number.parseInt(String(value).trim(), 10))
      .filter((value) => Number.isInteger(value) && value >= 0 && value < EXPERT_COUNT),
  )];

  return experts.slice(0, 64);
}

function routeHandler(req, res) {
  const requestedExperts = parseRequestedExperts(req);
  if (!requestedExperts.length) {
    return res.status(400).json({
      error: 'invalid_experts',
      message: 'Pass expert IDs between 0 and 255, for example ?experts=12,13,14.',
    });
  }

  const cutoff = Date.now() - ONLINE_WINDOW_MS;
  const placeholders = requestedExperts.map(() => '?').join(',');
  const candidates = db.prepare(`
    SELECT d.*,
           SUM(CASE WHEN de.expert_id IN (${placeholders}) THEN 1 ELSE 0 END) AS matches
    FROM devices d
    JOIN device_experts de ON de.device_id = d.id
    WHERE d.status = 'online' AND d.last_seen >= ?
    GROUP BY d.id
    HAVING matches > 0
    ORDER BY matches DESC, COALESCE(d.latency_ms, 999999) ASC, d.last_seen DESC
    LIMIT 5
  `).all(...requestedExperts, cutoff);

  const devices = candidates.map((device) => {
    const experts = assignedExperts(device.genesis_number);
    return {
      deviceId: device.id,
      endpoint: device.endpoint || `p2p://${device.id}`,
      experts: experts.filter((expert) => requestedExperts.includes(expert)),
      latencyMs: device.latency_ms,
      lastSeen: new Date(device.last_seen).toISOString(),
      simulated: true,
    };
  });

  // Phase 4 intentionally fills the route with demo peers. These are visibly
  // marked simulated and must be replaced by authenticated P2P discovery.
  for (let index = devices.length; index < 5; index += 1) {
    devices.push({
      deviceId: `demo-host-${String(index + 1).padStart(3, '0')}`,
      endpoint: `demo://host-${index + 1}`,
      experts: [requestedExperts[index % requestedExperts.length]],
      latencyMs: 38 + (index * 11),
      lastSeen: new Date().toISOString(),
      simulated: true,
    });
  }

  return res.json({
    ok: true,
    simulated: true,
    disclaimer: 'MVP routing data only. No model weights are transferred.',
    requestedExperts,
    devices,
  });
}

function genesisHandler(req, res) {
  const body = req.body || {};
  const name = cleanText(body.name, 100);
  const email = cleanText(body.email, 254).toLowerCase();
  const country = cleanText(body.country, 80);
  const device = cleanText(body.device, 160, null);
  const acceptedHostTerms = body.acceptedHostTerms === true || body.acceptedHostTerms === 'true';

  if (!name || !/^\S+@\S+\.\S+$/.test(email) || !country) {
    return res.status(400).json({
      error: 'invalid_application',
      message: 'Name, a valid email, and country are required.',
    });
  }
  if (!acceptedHostTerms) {
    return res.status(400).json({
      error: 'host_terms_required',
      message: 'Please confirm that this computer can host a 20 GB placeholder shard.',
    });
  }

  const existing = db
    .prepare('SELECT id FROM genesis_applications WHERE email = ?')
    .get(email);

  db.prepare(`
    INSERT INTO genesis_applications (
      name, email, country, device, accepted_host_terms, created_at
    ) VALUES (?, ?, ?, ?, 1, ?)
    ON CONFLICT(email) DO UPDATE SET
      name = excluded.name,
      country = excluded.country,
      device = excluded.device,
      accepted_host_terms = 1
  `).run(name, email, country, device, Date.now());

  const application = db
    .prepare('SELECT id, created_at FROM genesis_applications WHERE email = ?')
    .get(email);

  return res.status(existing ? 200 : 201).json({
    ok: true,
    applicationId: application.id,
    alreadyApplied: Boolean(existing),
    message: existing
      ? 'Your Genesis host application is already on the list.'
      : 'Application received. A place is earned only after a host registers successfully.',
  });
}

function statsHandler(_req, res) {
  return res.json(networkStats());
}

app.get('/health', (_req, res) => {
  res.json({ ok: true, service: 'ii-coordinator', time: new Date().toISOString() });
});

app.post(['/register', '/api/register'], registerHandler);
app.post(['/heartbeat', '/api/heartbeat'], heartbeatHandler);
app.get(['/stats', '/api/stats'], statsHandler);
app.get(['/route', '/api/route'], routeHandler);
app.post(['/genesis', '/api/genesis'], genesisHandler);

app.get(['/checkout/:plan', '/api/checkout/:plan'], async (req, res) => {
  const plan = req.params.plan;
  if (!['monthly', 'yearly'].includes(plan)) {
    return res.status(404).json({ error: 'unknown_plan', message: 'Choose monthly or yearly.' });
  }

  // A pre-created Dodo payment link is the simplest production setup.
  const directLinks = {
    monthly: process.env.DODO_MONTHLY_CHECKOUT_URL,
    yearly: process.env.DODO_YEARLY_CHECKOUT_URL,
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
    || (process.env.DODO_PAYMENTS_ENVIRONMENT === 'test_mode'
      ? 'https://test.dodopayments.com'
      : 'https://live.dodopayments.com')
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
        'Content-Type': 'application/json',
      },
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
      console.error('[coordinator] Dodo checkout failed:', response.status, checkout);
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
      console.log('[coordinator] heartbeat window: 90 seconds');
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
