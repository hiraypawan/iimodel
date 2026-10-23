'use strict';

const crypto = require('node:crypto');
const {
  bearerToken,
  createEncryptedStore,
  randomToken,
  requireConfiguredBearer,
  tokenHash,
} = require('./security');

const AUTH_CHALLENGE_TTL_MS = 15 * 60 * 1_000;
const ACCESS_TOKEN_TTL_MS = 90 * 24 * 60 * 60 * 1_000;

function clean(value, maximum, fallback = '') {
  if (typeof value !== 'string') return fallback;
  return value.trim().replace(/[\u0000-\u001f\u007f]/g, '').slice(0, maximum) || fallback;
}

function validEmail(value) {
  const email = clean(value, 254).toLowerCase();
  return /^\S+@\S+\.\S+$/.test(email) ? email : null;
}

function ipHash(req) {
  const salt = process.env.II_AUDIT_HASH_KEY || process.env.II_DATA_ENCRYPTION_KEY || 'local-unconfigured';
  return crypto.createHmac('sha256', salt).update(req.ip || req.socket.remoteAddress || 'unknown').digest('hex');
}

function seedPlans(db) {
  const now = Date.now();
  const plans = [
    {
      code: 'free', name: 'II Free', interval: 'none', amount: 0,
      product: null, jobs: 25, tokens: 25_000,
    },
    {
      code: 'monthly', name: 'II Pro Monthly', interval: 'month', amount: 24_900,
      product: process.env.DODO_MONTHLY_PRODUCT_ID || null, jobs: 2_000, tokens: 2_000_000,
    },
    {
      code: 'yearly', name: 'II Pro Yearly', interval: 'year', amount: 199_900,
      product: process.env.DODO_YEARLY_PRODUCT_ID || null, jobs: 30_000, tokens: 30_000_000,
    },
  ];
  const upsert = db.prepare(`
    INSERT INTO plans (
      code, name, billing_interval, currency, amount_minor, dodo_product_id,
      inference_jobs_quota, max_tokens_quota, active, created_at, updated_at
    ) VALUES (?, ?, ?, 'INR', ?, ?, ?, ?, 1, ?, ?)
    ON CONFLICT(code) DO UPDATE SET
      name = excluded.name,
      billing_interval = excluded.billing_interval,
      amount_minor = excluded.amount_minor,
      dodo_product_id = COALESCE(excluded.dodo_product_id, plans.dodo_product_id),
      inference_jobs_quota = excluded.inference_jobs_quota,
      max_tokens_quota = excluded.max_tokens_quota,
      updated_at = excluded.updated_at
  `);
  for (const plan of plans) {
    upsert.run(
      plan.code, plan.name, plan.interval, plan.amount, plan.product,
      plan.jobs, plan.tokens, now, now,
    );
  }
}

function findUserByBearer(db, req, { scope = null, touch = true } = {}) {
  const raw = bearerToken(req);
  if (!raw) return null;
  const now = Date.now();
  const row = db.prepare(`
    SELECT u.*, ut.id AS token_id, ut.scopes_json, ut.expires_at
    FROM user_tokens ut JOIN users u ON u.id = ut.user_id
    WHERE ut.token_hash = ? AND ut.revoked_at IS NULL
      AND (ut.expires_at IS NULL OR ut.expires_at > ?)
      AND u.status = 'active'
  `).get(tokenHash(raw), now);
  if (!row) return null;
  const scopes = JSON.parse(row.scopes_json || '[]');
  if (scope && !scopes.includes('*') && !scopes.includes(scope)) return null;
  if (touch) db.prepare('UPDATE user_tokens SET last_used_at = ? WHERE id = ?').run(now, row.token_id);
  return {
    id: row.id,
    email: row.email,
    display_name: row.display_name,
    country: row.country,
    email_verified_at: row.email_verified_at,
    token_id: row.token_id,
    scopes,
  };
}

function requireUser(db, scope = null) {
  return (req, res, next) => {
    const user = findUserByBearer(db, req, { scope });
    if (!user) {
      res.set('WWW-Authenticate', 'Bearer');
      return res.status(401).json({ error: 'user_authentication_required' });
    }
    req.user = user;
    return next();
  };
}

function issueUserToken(db, userId, {
  label = 'browser',
  scopes = ['jobs:submit', 'billing:read', 'profile:read'],
  expiresAt = Date.now() + ACCESS_TOKEN_TTL_MS,
} = {}) {
  const id = `token_${crypto.randomUUID()}`;
  const token = randomToken();
  db.prepare(`
    INSERT INTO user_tokens (
      id, user_id, token_hash, label, scopes_json, expires_at, created_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?)
  `).run(id, userId, tokenHash(token), clean(label, 80, 'browser'), JSON.stringify(scopes), expiresAt, Date.now());
  return { id, token, expiresAt, scopes };
}

function currentEntitlements(db, userId, now = Date.now()) {
  return db.prepare(`
    SELECT feature, allowance, used, period_start, period_end, status
    FROM entitlements
    WHERE user_id = ? AND period_start <= ? AND period_end > ? AND status = 'active'
    ORDER BY period_start DESC
  `).all(userId, now, now).map((row) => ({
    ...row,
    remaining: Math.max(0, row.allowance - row.used),
    period_start: new Date(row.period_start).toISOString(),
    period_end: new Date(row.period_end).toISOString(),
  }));
}

function grantPlanEntitlements(db, userId, planCode, subscriptionId, start, end) {
  const plan = db.prepare('SELECT * FROM plans WHERE code = ? AND active = 1').get(planCode);
  if (!plan) throw new Error(`Unknown active plan: ${planCode}`);
  const upsert = db.prepare(`
    INSERT INTO entitlements (
      user_id, feature, source_subscription_id, allowance, used,
      period_start, period_end, status, updated_at
    ) VALUES (?, ?, ?, ?, 0, ?, ?, 'active', ?)
    ON CONFLICT(user_id, feature, period_start) DO UPDATE SET
      source_subscription_id = excluded.source_subscription_id,
      allowance = excluded.allowance,
      period_end = excluded.period_end,
      status = 'active',
      updated_at = excluded.updated_at
  `);
  upsert.run(userId, 'inference_jobs', subscriptionId, plan.inference_jobs_quota, start, end, Date.now());
  upsert.run(userId, 'inference_tokens', subscriptionId, plan.max_tokens_quota, start, end, Date.now());
}

function installAccountRoutes(app, db) {
  const encryptedStore = createEncryptedStore();
  const requireAdmin = requireConfiguredBearer('II_ADMIN_API_TOKEN', 'admin_api_not_configured');
  seedPlans(db);

  app.get(['/plans', '/api/plans'], (_req, res) => {
    const plans = db.prepare(`
      SELECT code, name, billing_interval, currency, amount_minor,
             inference_jobs_quota, max_tokens_quota
      FROM plans WHERE active = 1 ORDER BY amount_minor
    `).all();
    return res.json({
      ok: true,
      amount_unit: 'smallest_currency_unit',
      plans,
    });
  });

  app.post('/api/auth/request-link', async (req, res) => {
    const email = validEmail(req.body?.email);
    if (!email) return res.status(400).json({ error: 'invalid_email' });
    const recent = db.prepare(`
      SELECT COUNT(*) AS count FROM identity_challenges
      WHERE email = ? AND created_at > ?
    `).get(email, Date.now() - 60 * 60 * 1_000).count;
    if (recent >= 5) return res.status(429).json({ error: 'identity_rate_limited' });
    const token = randomToken();
    const id = `identity_${crypto.randomUUID()}`;
    const expiresAt = Date.now() + AUTH_CHALLENGE_TTL_MS;
    db.prepare(`
      INSERT INTO identity_challenges (
        id, email, token_hash, expires_at, requested_ip_hash, created_at
      ) VALUES (?, ?, ?, ?, ?, ?)
    `).run(id, email, tokenHash(token), expiresAt, ipHash(req), Date.now());

    if (process.env.II_AUTH_TEST_MODE === 'true') {
      return res.status(202).json({
        ok: true,
        challenge_id: id,
        test_only_token: token,
        expires_at: new Date(expiresAt).toISOString(),
      });
    }
    const deliveryUrl = process.env.II_AUTH_DELIVERY_WEBHOOK_URL;
    const deliverySecret = process.env.II_AUTH_DELIVERY_WEBHOOK_TOKEN;
    if (!deliveryUrl || !deliverySecret) {
      db.prepare('DELETE FROM identity_challenges WHERE id = ?').run(id);
      return res.status(503).json({
        error: 'identity_delivery_not_configured',
        message: 'Configure the transactional email delivery webhook; login tokens are never returned in production.',
      });
    }
    try {
      const parsed = new URL(deliveryUrl);
      const local = parsed.protocol === 'http:' && ['127.0.0.1', 'localhost'].includes(parsed.hostname);
      if (parsed.protocol !== 'https:' && !local) throw new Error('delivery webhook must use HTTPS');
      const response = await fetch(parsed, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${deliverySecret}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          template: 'ii-login-link',
          email,
          challenge_id: id,
          token,
          expires_at: new Date(expiresAt).toISOString(),
        }),
        signal: AbortSignal.timeout(10_000),
      });
      if (!response.ok) throw new Error(`delivery returned ${response.status}`);
    } catch (error) {
      db.prepare('DELETE FROM identity_challenges WHERE id = ?').run(id);
      console.error('[identity] delivery failed:', error.message);
      return res.status(502).json({ error: 'identity_delivery_failed' });
    }
    return res.status(202).json({
      ok: true,
      challenge_id: id,
      expires_at: new Date(expiresAt).toISOString(),
    });
  });

  app.post('/api/auth/verify', (req, res) => {
    const challengeId = clean(req.body?.challenge_id, 100);
    const supplied = clean(req.body?.token, 300);
    const now = Date.now();
    const challenge = db.prepare('SELECT * FROM identity_challenges WHERE id = ?').get(challengeId);
    if (!challenge || challenge.consumed_at || challenge.expires_at <= now || challenge.attempts >= 5
        || challenge.token_hash !== tokenHash(supplied)) {
      if (challenge) db.prepare('UPDATE identity_challenges SET attempts = attempts + 1 WHERE id = ?').run(challenge.id);
      return res.status(401).json({ error: 'invalid_or_expired_identity_challenge' });
    }
    const result = db.transaction(() => {
      let user = db.prepare('SELECT * FROM users WHERE email = ?').get(challenge.email);
      if (!user) {
        const userId = `user_${crypto.randomUUID()}`;
        db.prepare(`
          INSERT INTO users (
            id, email, status, email_verified_at, created_at, updated_at
          ) VALUES (?, ?, 'active', ?, ?, ?)
        `).run(userId, challenge.email, now, now, now);
        user = db.prepare('SELECT * FROM users WHERE id = ?').get(userId);
        grantPlanEntitlements(db, userId, 'free', null, now, now + 30 * 24 * 60 * 60 * 1_000);
      } else {
        db.prepare(`UPDATE users SET email_verified_at = COALESCE(email_verified_at, ?), updated_at = ? WHERE id = ?`)
          .run(now, now, user.id);
      }
      db.prepare('UPDATE identity_challenges SET consumed_at = ? WHERE id = ?').run(now, challenge.id);
      const issued = issueUserToken(db, user.id);
      db.prepare(`
        INSERT INTO audit_events (
          actor_type, actor_id, action, target_type, target_id, details_json, ip_hash, created_at
        ) VALUES ('user', ?, 'identity.verify', 'user', ?, '{}', ?, ?)
      `).run(user.id, user.id, ipHash(req), now);
      return { user, issued };
    })();
    return res.json({
      ok: true,
      access_token: result.issued.token,
      token_id: result.issued.id,
      expires_at: new Date(result.issued.expiresAt).toISOString(),
      user: { id: result.user.id, email: result.user.email },
    });
  });

  app.get('/api/me', requireUser(db, 'profile:read'), (req, res) => {
    const subscriptions = db.prepare(`
      SELECT id, plan_code, provider, status, current_period_start, current_period_end,
             cancel_at_period_end, created_at, updated_at
      FROM subscriptions WHERE user_id = ? ORDER BY updated_at DESC
    `).all(req.user.id);
    const genesis = db.prepare(`
      SELECT gq.status, gq.device_id, gq.qualified_at, gh.genesis_number
      FROM genesis_qualifications gq
      LEFT JOIN genesis_hosts gh ON gh.device_id = gq.device_id
      WHERE gq.user_id = ?
    `).get(req.user.id) || null;
    return res.json({
      ok: true,
      user: {
        id: req.user.id,
        email: req.user.email,
        display_name: req.user.display_name,
        country: req.user.country,
      },
      subscriptions,
      entitlements: currentEntitlements(db, req.user.id),
      genesis,
    });
  });

  app.post('/api/tokens', requireUser(db, 'profile:read'), (req, res) => {
    const requestedScopes = Array.isArray(req.body?.scopes)
      ? req.body.scopes.filter((scope) => req.user.scopes.includes('*') || req.user.scopes.includes(scope))
      : req.user.scopes;
    if (!requestedScopes.length) return res.status(400).json({ error: 'empty_token_scopes' });
    const issued = issueUserToken(db, req.user.id, {
      label: clean(req.body?.label, 80, 'personal token'),
      scopes: [...new Set(requestedScopes)].slice(0, 20),
    });
    return res.status(201).json({
      ok: true,
      id: issued.id,
      access_token: issued.token,
      expires_at: new Date(issued.expiresAt).toISOString(),
      message: 'Save this token now. Only its SHA-256 hash is stored.',
    });
  });

  app.delete('/api/tokens/:id', requireUser(db, 'profile:read'), (req, res) => {
    const result = db.prepare(`
      UPDATE user_tokens SET revoked_at = ? WHERE id = ? AND user_id = ? AND revoked_at IS NULL
    `).run(Date.now(), req.params.id, req.user.id);
    if (!result.changes) return res.status(404).json({ error: 'token_not_found' });
    return res.json({ ok: true, revoked: true });
  });

  app.post('/api/push-subscriptions', requireUser(db, 'profile:read'), (req, res) => {
    if (!encryptedStore.configured) return res.status(503).json({ error: 'encryption_not_configured' });
    const endpoint = clean(req.body?.endpoint, 2_048);
    const keys = req.body?.keys && typeof req.body.keys === 'object' ? req.body.keys : null;
    try {
      const parsed = new URL(endpoint);
      if (parsed.protocol !== 'https:') throw new Error('not https');
    } catch {
      return res.status(400).json({ error: 'invalid_push_subscription' });
    }
    if (!keys?.p256dh || !keys?.auth) return res.status(400).json({ error: 'invalid_push_subscription' });
    const endpointHash = tokenHash(endpoint);
    const existing = db.prepare('SELECT id FROM push_subscriptions WHERE endpoint_hash = ?').get(endpointHash);
    const id = existing?.id || `push_${crypto.randomUUID()}`;
    db.prepare(`
      INSERT INTO push_subscriptions (
        id, user_id, endpoint_hash, endpoint_encrypted, keys_encrypted, created_at
      ) VALUES (?, ?, ?, ?, ?, ?)
      ON CONFLICT(endpoint_hash) DO UPDATE SET
        user_id = excluded.user_id,
        endpoint_encrypted = excluded.endpoint_encrypted,
        keys_encrypted = excluded.keys_encrypted,
        disabled_at = NULL
    `).run(
      id,
      req.user.id,
      endpointHash,
      encryptedStore.encrypt(endpoint, `push:${id}:endpoint`),
      encryptedStore.encrypt(JSON.stringify(keys), `push:${id}:keys`),
      Date.now(),
    );
    return res.status(existing ? 200 : 201).json({
      ok: true,
      id,
      delivery: 'requires_configured_web_push_sender',
    });
  });

  app.post('/admin/users', requireAdmin, (req, res) => {
    const email = validEmail(req.body?.email);
    if (!email) return res.status(400).json({ error: 'invalid_email' });
    const now = Date.now();
    const id = `user_${crypto.randomUUID()}`;
    try {
      db.prepare(`
        INSERT INTO users (
          id, email, display_name, country, status, email_verified_at, created_at, updated_at
        ) VALUES (?, ?, ?, ?, 'active', ?, ?, ?)
      `).run(
        id,
        email,
        clean(req.body?.display_name, 120, null),
        /^[A-Z]{2}$/.test(String(req.body?.country || '').toUpperCase())
          ? String(req.body.country).toUpperCase()
          : null,
        req.body?.email_verified === true ? now : null,
        now,
        now,
      );
      grantPlanEntitlements(db, id, 'free', null, now, now + 30 * 24 * 60 * 60 * 1_000);
    } catch (error) {
      if (error.code === 'SQLITE_CONSTRAINT_UNIQUE') return res.status(409).json({ error: 'user_exists' });
      throw error;
    }
    return res.status(201).json({ ok: true, id, email });
  });

  app.post('/admin/genesis/qualify', requireAdmin, (req, res) => {
    const user = db.prepare('SELECT id FROM users WHERE id = ?').get(req.body?.user_id);
    const host = db.prepare(`
      SELECT gh.device_id, gh.genesis_number, d.status
      FROM genesis_hosts gh JOIN devices d ON d.id = gh.device_id
      WHERE gh.device_id = ? AND gh.genesis_number <= 400
    `).get(req.body?.device_id);
    if (!user || !host) {
      return res.status(409).json({
        error: 'genesis_not_qualified',
        message: 'A real user and cryptographically verified first-400 artifact host are required.',
      });
    }
    const now = Date.now();
    db.prepare(`
      INSERT INTO genesis_qualifications (
        user_id, device_id, status, qualified_at, reason, updated_at
      ) VALUES (?, ?, 'qualified', ?, ?, ?)
      ON CONFLICT(user_id) DO UPDATE SET
        device_id = excluded.device_id, status = 'qualified',
        qualified_at = excluded.qualified_at, reason = excluded.reason,
        updated_at = excluded.updated_at
    `).run(user.id, host.device_id, now, clean(req.body?.reason, 300, 'Verified Genesis host.'), now);
    return res.json({
      ok: true,
      user_id: user.id,
      device_id: host.device_id,
      genesis_number: host.genesis_number,
      status: 'qualified',
    });
  });
}

module.exports = {
  currentEntitlements,
  findUserByBearer,
  grantPlanEntitlements,
  installAccountRoutes,
  issueUserToken,
  requireUser,
  seedPlans,
  validEmail,
};
