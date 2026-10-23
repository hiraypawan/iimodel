'use strict';

const crypto = require('node:crypto');
const {
  createEncryptedStore,
  randomToken,
  requireConfiguredBearer,
  secureTokenMatch,
  tokenHash,
} = require('./security');
const {
  findUserByBearer,
  grantPlanEntitlements,
  requireUser,
  seedPlans,
} = require('./accounts');

const WEBHOOK_TOLERANCE_SECONDS = 5 * 60;

function clean(value, maximum, fallback = '') {
  if (typeof value !== 'string') return fallback;
  return value.trim().replace(/[\u0000-\u001f\u007f]/g, '').slice(0, maximum) || fallback;
}

function dodoBaseUrl() {
  return (
    process.env.DODO_PAYMENTS_BASE_URL
    || (process.env.DODO_PAYMENTS_ENVIRONMENT === 'live_mode'
      ? 'https://live.dodopayments.com'
      : 'https://test.dodopayments.com')
  ).replace(/\/$/, '');
}

function checkoutUrl(value) {
  try {
    const parsed = new URL(value);
    return parsed.protocol === 'https:' ? parsed.toString() : null;
  } catch {
    return null;
  }
}

function webhookSecretBytes(secret) {
  const encoded = String(secret || '').startsWith('whsec_')
    ? String(secret).slice('whsec_'.length)
    : String(secret || '');
  const key = Buffer.from(encoded, 'base64');
  if (!key.length) throw new Error('Dodo webhook secret is empty or invalid.');
  return key;
}

function verifyStandardWebhook(rawBody, headers, secret, now = Date.now()) {
  const id = clean(headers['webhook-id'], 200);
  const timestampText = clean(headers['webhook-timestamp'], 30);
  const supplied = clean(headers['webhook-signature'], 4_000);
  if (!id || !timestampText || !supplied) throw new Error('Missing Standard Webhooks headers.');
  if (!/^\d+$/.test(timestampText)) throw new Error('Invalid webhook timestamp.');
  const timestamp = Number(timestampText);
  const nowSeconds = Math.floor(now / 1_000);
  if (!Number.isSafeInteger(timestamp)
      || Math.abs(nowSeconds - timestamp) > WEBHOOK_TOLERANCE_SECONDS) {
    throw new Error('Webhook timestamp is outside the five-minute replay window.');
  }
  const message = `${id}.${timestamp}.${rawBody.toString('utf8')}`;
  const expected = crypto.createHmac('sha256', webhookSecretBytes(secret))
    .update(message)
    .digest('base64');
  const matched = supplied.split(' ').some((versioned) => {
    const [version, signature] = versioned.split(',');
    if (version !== 'v1' || !signature) return false;
    const left = Buffer.from(signature);
    const right = Buffer.from(expected);
    return left.length === right.length && crypto.timingSafeEqual(left, right);
  });
  if (!matched) throw new Error('Webhook signature did not match.');
  return { id, timestamp };
}

function parseTimestamp(value, fallback = null) {
  if (!value) return fallback;
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) ? timestamp : fallback;
}

function subscriptionStatus(eventType) {
  return ({
    'subscription.active': 'active',
    'subscription.renewed': 'renewed',
    'subscription.on_hold': 'on_hold',
    'subscription.past_due': 'past_due',
    'subscription.paused': 'paused',
    'subscription.unpaused': 'active',
    'subscription.plan_changed': 'active',
    'subscription.cancelled': 'cancelled',
    'subscription.failed': 'past_due',
    'subscription.expired': 'expired',
    'refund.succeeded': 'refunded',
    'dispute.opened': 'chargeback',
    'dispute.lost': 'chargeback',
  })[eventType] || null;
}

function installBillingRoutes(app, db) {
  const encryptedStore = createEncryptedStore();
  const requireAdmin = requireConfiguredBearer('II_ADMIN_API_TOKEN', 'admin_api_not_configured');
  seedPlans(db);

  function publicWebUrl(req) {
    return (process.env.PUBLIC_WEB_URL || `${req.protocol}://${req.get('host')}`).replace(/\/$/, '');
  }

  function planDetails(code) {
    return db.prepare('SELECT * FROM plans WHERE code = ? AND active = 1').get(code);
  }

  async function createCheckout(req, res, planCode, user = null, { redirect = false } = {}) {
    const plan = planDetails(planCode);
    if (!plan || !['monthly', 'yearly'].includes(plan.code)) {
      return res.status(404).json({ error: 'unknown_plan', message: 'Choose monthly or yearly.' });
    }
    const directLink = plan.code === 'monthly'
      ? process.env.DODO_MONTHLY_URL || process.env.DODO_MONTHLY_CHECKOUT_URL
      : process.env.DODO_YEARLY_URL || process.env.DODO_YEARLY_CHECKOUT_URL;
    const productId = plan.dodo_product_id
      || (plan.code === 'monthly' ? process.env.DODO_MONTHLY_PRODUCT_ID : process.env.DODO_YEARLY_PRODUCT_ID);
    const apiKey = process.env.DODO_PAYMENTS_API_KEY;
    const localId = `checkout_${crypto.randomUUID()}`;
    const correlation = randomToken();
    const now = Date.now();

    if (directLink && checkoutUrl(directLink) && (!user || !apiKey || !productId)) {
      if (user) {
        db.prepare(`
          INSERT INTO checkout_sessions (
            id, user_id, plan_code, checkout_url, status,
            correlation_token_hash, created_at, updated_at
          ) VALUES (?, ?, ?, ?, 'created', ?, ?, ?)
        `).run(localId, user.id, plan.code, checkoutUrl(directLink), tokenHash(correlation), now, now);
      }
      if (redirect) return res.redirect(302, checkoutUrl(directLink));
      return res.status(201).json({
        ok: true,
        id: user ? localId : null,
        checkout_url: checkoutUrl(directLink),
        correlation: user ? 'precreated_link_requires_matching_dodo_metadata' : 'anonymous',
      });
    }

    if (!apiKey || !productId) {
      return res.status(503).json({
        error: 'checkout_not_configured',
        message: `Set a Dodo checkout URL or DODO_${plan.code.toUpperCase()}_PRODUCT_ID with DODO_PAYMENTS_API_KEY.`,
        pricingMode: 'by_country',
      });
    }

    const metadata = user ? {
      ii_checkout_id: localId,
      ii_user_id: user.id,
      ii_correlation: correlation,
      plan: plan.code,
      source: 'ii-authenticated-web',
    } : { plan: plan.code, source: 'ii-web' };
    const body = {
      product_cart: [{ product_id: productId, quantity: 1 }],
      return_url: `${publicWebUrl(req)}/?checkout=success`,
      cancel_url: `${publicWebUrl(req)}/#pricing`,
      short_link: true,
      metadata,
      ...(user ? {
        customer: { email: user.email, ...(user.display_name ? { name: user.display_name } : {}) },
      } : {}),
    };

    try {
      const response = await fetch(`${dodoBaseUrl()}/checkouts`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${apiKey}`,
          Accept: 'application/json',
          'Content-Type': 'application/json',
        },
        signal: AbortSignal.timeout(15_000),
        body: JSON.stringify(body),
      });
      const provider = await response.json().catch(() => ({}));
      const url = checkoutUrl(provider.checkout_url);
      const providerId = clean(provider.session_id || provider.checkout_session_id || provider.id, 200, null);
      if (!response.ok || !url) {
        console.error('[billing] Dodo checkout failed:', response.status, clean(provider?.message || provider?.error, 200, 'unknown'));
        return res.status(502).json({
          error: 'checkout_provider_error',
          message: 'Dodo Payments could not create a checkout. Please try again.',
        });
      }
      if (user) {
        db.prepare(`
          INSERT INTO checkout_sessions (
            id, user_id, plan_code, provider_checkout_id, checkout_url, status,
            correlation_token_hash, provider_payload_hash, created_at, updated_at
          ) VALUES (?, ?, ?, ?, ?, 'created', ?, ?, ?, ?)
        `).run(
          localId,
          user.id,
          plan.code,
          providerId,
          url,
          tokenHash(correlation),
          crypto.createHash('sha256').update(JSON.stringify(provider)).digest('hex'),
          now,
          now,
        );
      }
      if (redirect) return res.redirect(303, url);
      return res.status(201).json({ ok: true, id: user ? localId : null, checkout_url: url });
    } catch (error) {
      console.error('[billing] Dodo API unavailable:', error.message);
      return res.status(502).json({
        error: 'checkout_provider_unavailable',
        message: 'Dodo Payments is temporarily unavailable. Please try again.',
      });
    }
  }

  app.get(['/checkout/:plan', '/api/checkout/:plan'], (req, res) => (
    createCheckout(req, res, req.params.plan, null, { redirect: true })
  ));

  app.post('/api/billing/checkout', requireUser(db, 'billing:read'), (req, res) => (
    createCheckout(req, res, req.body?.plan, req.user)
  ));

  app.get('/api/billing/status', requireUser(db, 'billing:read'), (req, res) => {
    const checkouts = db.prepare(`
      SELECT id, plan_code, status, checkout_url, created_at, updated_at
      FROM checkout_sessions WHERE user_id = ? ORDER BY created_at DESC LIMIT 20
    `).all(req.user.id);
    const subscriptions = db.prepare(`
      SELECT id, plan_code, status, current_period_start, current_period_end,
             cancel_at_period_end, created_at, updated_at
      FROM subscriptions WHERE user_id = ? ORDER BY updated_at DESC
    `).all(req.user.id);
    return res.json({ ok: true, checkouts, subscriptions });
  });

  function fraudFlag(userId, eventId, kind, severity, details, now) {
    db.prepare(`
      INSERT INTO fraud_flags (
        id, user_id, provider_event_id, kind, severity, status, details_json, created_at
      ) VALUES (?, ?, ?, ?, ?, 'open', ?, ?)
    `).run(`fraud_${crypto.randomUUID()}`, userId, eventId, kind, severity, JSON.stringify(details), now);
  }

  function findCorrelatedUser(event) {
    const data = event.data || {};
    const metadata = data.metadata && typeof data.metadata === 'object' ? data.metadata : {};
    const checkoutId = clean(metadata.ii_checkout_id, 100, null);
    const correlation = clean(metadata.ii_correlation, 300, null);
    if (checkoutId && correlation) {
      const checkout = db.prepare(`
        SELECT cs.*, u.email, u.display_name
        FROM checkout_sessions cs JOIN users u ON u.id = cs.user_id
        WHERE cs.id = ?
      `).get(checkoutId);
      if (checkout && checkout.correlation_token_hash === tokenHash(correlation)
          && (!metadata.ii_user_id || metadata.ii_user_id === checkout.user_id)) {
        return { userId: checkout.user_id, checkout, metadata };
      }
      return { userId: null, checkout: null, metadata, correlationFailed: true };
    }
    const subscriptionId = clean(data.subscription_id || data.id, 200, null);
    if (subscriptionId) {
      const subscription = db.prepare(`
        SELECT * FROM subscriptions WHERE provider_subscription_id = ?
      `).get(subscriptionId);
      if (subscription) return { userId: subscription.user_id, checkout: null, metadata };
    }
    const paymentId = clean(
      data.payment_id || (data.payload_type === 'Payment' ? data.id : null),
      200,
      null,
    );
    if (paymentId) {
      const payment = db.prepare(`
        SELECT * FROM provider_payments
        WHERE provider = 'dodo' AND provider_payment_id = ?
      `).get(paymentId);
      if (payment) return { userId: payment.user_id, checkout: null, metadata, payment };
    }
    const customerId = clean(data.customer?.customer_id || data.customer_id, 200, null);
    if (customerId) {
      const customer = db.prepare(`
        SELECT user_id FROM provider_customers WHERE provider = 'dodo' AND provider_customer_id = ?
      `).get(customerId);
      if (customer) return { userId: customer.user_id, checkout: null, metadata };
    }
    return { userId: null, checkout: null, metadata };
  }

  const processEvent = db.transaction((eventId, event, payloadHash, rawText, now) => {
    const encrypted = encryptedStore.encrypt(rawText, `payment-event:dodo:${eventId}`);
    const inserted = db.prepare(`
      INSERT OR IGNORE INTO payment_events (
        provider, provider_event_id, event_type, payload_hash, payload_encrypted,
        status, received_at
      ) VALUES ('dodo', ?, ?, ?, ?, 'received', ?)
    `).run(eventId, clean(event.type, 100, 'unknown'), payloadHash, encrypted, now);
    if (!inserted.changes) {
      const existing = db.prepare(`
        SELECT payload_hash, status FROM payment_events
        WHERE provider = 'dodo' AND provider_event_id = ?
      `).get(eventId);
      if (existing.payload_hash !== payloadHash) {
        fraudFlag(null, eventId, 'webhook_id_payload_mismatch', 'critical', {}, now);
        return { duplicate: true, mismatch: true };
      }
      return { duplicate: true, status: existing.status };
    }

    const correlation = findCorrelatedUser(event);
    if (correlation.correlationFailed) {
      fraudFlag(null, eventId, 'checkout_correlation_failed', 'high', {
        checkout_id: correlation.metadata.ii_checkout_id,
      }, now);
      db.prepare(`
        UPDATE payment_events SET status = 'ignored', error = ?, processed_at = ? WHERE provider_event_id = ?
      `).run('Checkout correlation did not match.', now, eventId);
      return { ignored: true, reason: 'correlation_failed' };
    }
    const data = event.data || {};
    const userId = correlation.userId;
    if (correlation.checkout && ['payment.succeeded', 'subscription.active', 'subscription.renewed'].includes(event.type)) {
      db.prepare(`
        UPDATE checkout_sessions SET status = 'completed', updated_at = ? WHERE id = ?
      `).run(now, correlation.checkout.id);
    }
    const customerId = clean(data.customer?.customer_id || data.customer_id, 200, null);
    if (userId && customerId) {
      db.prepare(`
        INSERT INTO provider_customers (user_id, provider, provider_customer_id, created_at)
        VALUES (?, 'dodo', ?, ?)
        ON CONFLICT(provider, provider_customer_id) DO NOTHING
      `).run(userId, customerId, now);
    }

    const nextStatus = subscriptionStatus(event.type);
    let providerSubscriptionId = clean(
      data.subscription_id || (data.payload_type === 'Subscription' ? data.id : null),
      200,
      null,
    );
    let subscription = providerSubscriptionId
      ? db.prepare('SELECT * FROM subscriptions WHERE provider_subscription_id = ?').get(providerSubscriptionId)
      : correlation.payment?.subscription_id
        ? db.prepare('SELECT * FROM subscriptions WHERE id = ?').get(correlation.payment.subscription_id)
        : null;
    if (!providerSubscriptionId && subscription) providerSubscriptionId = subscription.provider_subscription_id;
    const productId = clean(data.product_id || data.product?.product_id, 200, null);
    const plan = correlation.checkout
      ? db.prepare('SELECT * FROM plans WHERE code = ?').get(correlation.checkout.plan_code)
      : productId
        ? db.prepare('SELECT * FROM plans WHERE dodo_product_id = ?').get(productId)
        : subscription
          ? db.prepare('SELECT * FROM plans WHERE code = ?').get(subscription.plan_code)
          : null;

    if (nextStatus && userId && providerSubscriptionId && plan) {
      const periodStart = parseTimestamp(
        data.previous_billing_date || data.current_period_start || data.created_at,
        now,
      );
      const defaultDuration = plan.billing_interval === 'year'
        ? 365 * 24 * 60 * 60 * 1_000
        : 31 * 24 * 60 * 60 * 1_000;
      const periodEnd = parseTimestamp(
        data.next_billing_date || data.current_period_end || data.cancelled_at,
        periodStart + defaultDuration,
      );
      if (!subscription) {
        const id = `subscription_${crypto.randomUUID()}`;
        db.prepare(`
          INSERT INTO subscriptions (
            id, user_id, plan_code, provider, provider_customer_id,
            provider_subscription_id, status, current_period_start,
            current_period_end, cancel_at_period_end, created_at, updated_at
          ) VALUES (?, ?, ?, 'dodo', ?, ?, ?, ?, ?, ?, ?, ?)
        `).run(
          id,
          userId,
          plan.code,
          customerId,
          providerSubscriptionId,
          nextStatus,
          periodStart,
          periodEnd,
          data.cancel_at_next_billing_date ? 1 : 0,
          now,
          now,
        );
        subscription = db.prepare('SELECT * FROM subscriptions WHERE id = ?').get(id);
      } else {
        db.prepare(`
          UPDATE subscriptions SET plan_code = ?, provider_customer_id = COALESCE(?, provider_customer_id),
            status = ?, current_period_start = ?, current_period_end = ?,
            cancel_at_period_end = ?, updated_at = ? WHERE id = ?
        `).run(
          plan.code,
          customerId,
          nextStatus,
          periodStart,
          periodEnd,
          data.cancel_at_next_billing_date ? 1 : 0,
          now,
          subscription.id,
        );
      }
      if (['active', 'renewed'].includes(nextStatus)) {
        grantPlanEntitlements(db, userId, plan.code, subscription.id, periodStart, periodEnd);
      } else if (['expired', 'refunded', 'chargeback', 'on_hold', 'past_due'].includes(nextStatus)) {
        db.prepare(`
          UPDATE entitlements SET status = 'suspended', updated_at = ?
          WHERE source_subscription_id = ? AND status = 'active'
        `).run(now, subscription.id);
      } else if (nextStatus === 'cancelled') {
        db.prepare(`
          UPDATE entitlements SET status = 'expired', period_end = MIN(period_end, ?), updated_at = ?
          WHERE source_subscription_id = ? AND status = 'active'
        `).run(now, now, subscription.id);
      }
    }

    const providerPaymentId = clean(
      data.payment_id || (data.payload_type === 'Payment' ? data.id : null),
      200,
      null,
    );
    if (userId && providerPaymentId) {
      const paymentStatus = event.type === 'payment.succeeded'
        ? 'succeeded'
        : event.type === 'payment.failed'
          ? 'failed'
          : event.type === 'refund.succeeded'
            ? 'refunded'
            : event.type.startsWith('dispute.')
              ? 'disputed'
              : clean(data.status, 40, 'observed');
      db.prepare(`
        INSERT INTO provider_payments (
          provider, provider_payment_id, user_id, subscription_id, checkout_id,
          status, amount_minor, currency, updated_at
        ) VALUES ('dodo', ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(provider, provider_payment_id) DO UPDATE SET
          user_id = excluded.user_id,
          subscription_id = COALESCE(excluded.subscription_id, provider_payments.subscription_id),
          checkout_id = COALESCE(excluded.checkout_id, provider_payments.checkout_id),
          status = excluded.status,
          amount_minor = COALESCE(excluded.amount_minor, provider_payments.amount_minor),
          currency = COALESCE(excluded.currency, provider_payments.currency),
          updated_at = excluded.updated_at
      `).run(
        providerPaymentId,
        userId,
        subscription?.id || correlation.payment?.subscription_id || null,
        correlation.checkout?.id || correlation.payment?.checkout_id || null,
        paymentStatus,
        Number.isSafeInteger(data.total_amount) ? data.total_amount
          : Number.isSafeInteger(data.amount) ? data.amount : null,
        clean(data.currency, 8, null),
        now,
      );
    }

    if (['refund.succeeded', 'dispute.opened', 'dispute.lost'].includes(event.type)) {
      fraudFlag(userId, eventId, event.type.startsWith('dispute') ? 'chargeback_or_dispute' : 'refund', event.type.startsWith('dispute') ? 'critical' : 'medium', {
        provider_subscription_id: providerSubscriptionId,
      }, now);
    }
    db.prepare(`
      INSERT INTO audit_events (
        actor_type, actor_id, action, target_type, target_id, details_json, created_at
      ) VALUES ('provider', 'dodo', ?, 'payment_event', ?, ?, ?)
    `).run(`dodo.${event.type}`, eventId, JSON.stringify({ user_id: userId }), now);
    db.prepare(`
      UPDATE payment_events SET status = 'processed', processed_at = ? WHERE provider_event_id = ?
    `).run(now, eventId);
    return { processed: true, userId, subscriptionId: subscription?.id || null };
  });

  app.post('/webhooks/dodo', (req, res) => {
    if (!encryptedStore.configured) return res.status(503).json({ error: 'encryption_not_configured' });
    const secret = process.env.DODO_PAYMENTS_WEBHOOK_KEY;
    if (!secret) return res.status(503).json({ error: 'dodo_webhook_not_configured' });
    const rawBody = req.rawBody;
    if (!Buffer.isBuffer(rawBody)) return res.status(500).json({ error: 'raw_webhook_body_unavailable' });
    let verified;
    try {
      verified = verifyStandardWebhook(rawBody, req.headers, secret);
    } catch (error) {
      return res.status(401).json({ error: 'invalid_webhook_signature', message: error.message });
    }
    let event;
    try {
      event = JSON.parse(rawBody.toString('utf8'));
    } catch {
      return res.status(400).json({ error: 'invalid_webhook_json' });
    }
    if (!event || typeof event.type !== 'string' || !event.data || typeof event.data !== 'object') {
      return res.status(400).json({ error: 'invalid_webhook_event' });
    }
    const rawText = rawBody.toString('utf8');
    const payloadHash = crypto.createHash('sha256').update(rawBody).digest('hex');
    try {
      const result = processEvent(verified.id, event, payloadHash, rawText, Date.now());
      if (result.mismatch) return res.status(409).json({ error: 'webhook_replay_payload_mismatch' });
      return res.json({ ok: true, duplicate: Boolean(result.duplicate), processed: Boolean(result.processed) });
    } catch (error) {
      console.error('[billing] webhook processing failed:', error.message);
      db.prepare(`
        UPDATE payment_events SET status = 'failed', error = ?, processed_at = ?
        WHERE provider = 'dodo' AND provider_event_id = ?
      `).run(clean(error.message, 500), Date.now(), verified.id);
      return res.status(500).json({ error: 'webhook_processing_failed' });
    }
  });

  app.get('/admin/billing/events', requireAdmin, (_req, res) => {
    const events = db.prepare(`
      SELECT provider_event_id, event_type, payload_hash, status, error, received_at, processed_at
      FROM payment_events ORDER BY received_at DESC LIMIT 500
    `).all();
    const flags = db.prepare(`
      SELECT id, user_id, provider_event_id, kind, severity, status, details_json, created_at, resolved_at
      FROM fraud_flags ORDER BY created_at DESC LIMIT 500
    `).all().map((row) => ({ ...row, details: JSON.parse(row.details_json), details_json: undefined }));
    return res.json({ ok: true, payload_plaintext_included: false, events, fraud_flags: flags });
  });

  app.post('/admin/billing/fraud/:id/resolve', requireAdmin, (req, res) => {
    const status = req.body?.status;
    if (!['resolved', 'dismissed'].includes(status)) return res.status(400).json({ error: 'invalid_fraud_status' });
    const result = db.prepare(`
      UPDATE fraud_flags SET status = ?, resolved_at = ? WHERE id = ? AND status IN ('open', 'reviewing')
    `).run(status, Date.now(), req.params.id);
    if (!result.changes) return res.status(404).json({ error: 'fraud_flag_not_found' });
    return res.json({ ok: true, id: req.params.id, status });
  });
}

module.exports = {
  installBillingRoutes,
  subscriptionStatus,
  verifyStandardWebhook,
  webhookSecretBytes,
};
