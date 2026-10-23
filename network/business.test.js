'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const fsp = require('node:fs/promises');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');

const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'ii-business-test-'));
const webhookKey = crypto.randomBytes(32);
process.env.II_DB_PATH = path.join(directory, 'network.db');
process.env.II_DATA_ENCRYPTION_KEY = crypto.randomBytes(32).toString('base64');
process.env.II_ADMIN_API_TOKEN = 'business-admin-token-32-bytes-long';
process.env.II_AUTH_TEST_MODE = 'true';
process.env.DODO_PAYMENTS_API_KEY = 'dodo-test-key';
process.env.DODO_MONTHLY_PRODUCT_ID = 'pdt_monthly_business';
process.env.DODO_YEARLY_PRODUCT_ID = 'pdt_yearly_business';
process.env.DODO_PAYMENTS_WEBHOOK_KEY = `whsec_${webhookKey.toString('base64')}`;
const { app, db } = require('./server');

function listen(server) {
  return new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => resolve(server.address()));
  });
}

async function json(url, options = {}) {
  const response = await fetch(url, options);
  const body = await response.json();
  return { response, body };
}

function headers(token) {
  return {
    'Content-Type': 'application/json',
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
}

function webhookHeaders(id, body, timestamp = Math.floor(Date.now() / 1_000)) {
  const signature = crypto.createHmac('sha256', webhookKey)
    .update(`${id}.${timestamp}.${body}`)
    .digest('base64');
  return {
    'Content-Type': 'application/json',
    'webhook-id': id,
    'webhook-timestamp': String(timestamp),
    'webhook-signature': `v1,${signature}`,
  };
}

test('identity, correlated Dodo lifecycle, entitlements, idempotency, audit, and fraud flags', async (t) => {
  let checkoutRequest;
  const dodo = http.createServer(async (req, res) => {
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    checkoutRequest = JSON.parse(Buffer.concat(chunks).toString('utf8'));
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({
      checkout_session_id: 'cks_business',
      checkout_url: 'https://checkout.dodopayments.com/cks_business',
    }));
  });
  const dodoAddress = await listen(dodo);
  process.env.DODO_PAYMENTS_BASE_URL = `http://127.0.0.1:${dodoAddress.port}`;
  process.env.PUBLIC_WEB_URL = 'https://ii.example';

  const coordinator = http.createServer(app);
  const address = await listen(coordinator);
  const base = `http://127.0.0.1:${address.port}`;
  t.after(async () => {
    await new Promise((resolve) => coordinator.close(resolve));
    await new Promise((resolve) => dodo.close(resolve));
    db.close();
    await fsp.rm(directory, { recursive: true, force: true });
  });

  const plans = await json(`${base}/api/plans`);
  assert.equal(plans.body.amount_unit, 'smallest_currency_unit');
  assert.equal(plans.body.plans.find((plan) => plan.code === 'monthly').amount_minor, 24_900);

  const requested = await json(`${base}/api/auth/request-link`, {
    method: 'POST',
    headers: headers(),
    body: JSON.stringify({ email: 'subscriber@example.com' }),
  });
  assert.equal(requested.response.status, 202);
  const verified = await json(`${base}/api/auth/verify`, {
    method: 'POST',
    headers: headers(),
    body: JSON.stringify({
      challenge_id: requested.body.challenge_id,
      token: requested.body.test_only_token,
    }),
  });
  assert.equal(verified.response.status, 200);
  const accessToken = verified.body.access_token;
  assert.ok(accessToken.length > 30);
  assert.notEqual(
    db.prepare('SELECT token_hash FROM user_tokens WHERE id = ?').get(verified.body.token_id).token_hash,
    accessToken,
  );

  const checkout = await json(`${base}/api/billing/checkout`, {
    method: 'POST',
    headers: headers(accessToken),
    body: JSON.stringify({ plan: 'monthly' }),
  });
  assert.equal(checkout.response.status, 201);
  assert.equal(checkout.body.checkout_url, 'https://checkout.dodopayments.com/cks_business');
  assert.equal(checkoutRequest.customer.email, 'subscriber@example.com');
  assert.equal(checkoutRequest.product_cart[0].product_id, 'pdt_monthly_business');
  assert.equal(checkoutRequest.metadata.ii_checkout_id, checkout.body.id);
  assert.ok(checkoutRequest.metadata.ii_correlation);

  const activeEvent = JSON.stringify({
    business_id: 'biz_test',
    type: 'subscription.active',
    timestamp: new Date().toISOString(),
    data: {
      payload_type: 'Subscription',
      id: 'sub_business',
      customer: { customer_id: 'cus_business', email: 'subscriber@example.com', name: 'Subscriber' },
      product_id: 'pdt_monthly_business',
      metadata: checkoutRequest.metadata,
      created_at: new Date().toISOString(),
      next_billing_date: new Date(Date.now() + 30 * 24 * 60 * 60 * 1_000).toISOString(),
    },
  });
  const delivered = await json(`${base}/webhooks/dodo`, {
    method: 'POST',
    headers: webhookHeaders('evt_active', activeEvent),
    body: activeEvent,
  });
  assert.equal(delivered.response.status, 200);
  assert.equal(delivered.body.processed, true);
  const replay = await json(`${base}/webhooks/dodo`, {
    method: 'POST',
    headers: webhookHeaders('evt_active', activeEvent),
    body: activeEvent,
  });
  assert.equal(replay.body.duplicate, true);
  assert.equal(db.prepare('SELECT COUNT(*) AS count FROM payment_events').get().count, 1);

  const me = await json(`${base}/api/me`, { headers: { Authorization: `Bearer ${accessToken}` } });
  assert.equal(me.body.subscriptions[0].status, 'active');
  assert.equal(me.body.subscriptions[0].plan_code, 'monthly');
  assert.equal(
    me.body.entitlements.find((item) => item.feature === 'inference_jobs').allowance,
    2_000,
  );

  const disputeEvent = JSON.stringify({
    business_id: 'biz_test',
    type: 'dispute.opened',
    timestamp: new Date().toISOString(),
    data: {
      payload_type: 'Dispute',
      id: 'dispute_1',
      subscription_id: 'sub_business',
      customer_id: 'cus_business',
    },
  });
  const disputed = await json(`${base}/webhooks/dodo`, {
    method: 'POST',
    headers: webhookHeaders('evt_dispute', disputeEvent),
    body: disputeEvent,
  });
  assert.equal(disputed.response.status, 200);
  assert.equal(
    db.prepare("SELECT status FROM subscriptions WHERE provider_subscription_id = 'sub_business'").get().status,
    'chargeback',
  );
  assert.equal(db.prepare('SELECT severity FROM fraud_flags WHERE provider_event_id = ?').get('evt_dispute').severity, 'critical');
  assert.equal(
    db.prepare("SELECT status FROM entitlements WHERE source_subscription_id IS NOT NULL LIMIT 1").get().status,
    'suspended',
  );

  const invalid = await json(`${base}/webhooks/dodo`, {
    method: 'POST',
    headers: {
      ...webhookHeaders('evt_bad', activeEvent),
      'webhook-signature': 'v1,invalid',
    },
    body: activeEvent,
  });
  assert.equal(invalid.response.status, 401);

  const admin = await json(`${base}/admin/billing/events`, {
    headers: { Authorization: `Bearer ${process.env.II_ADMIN_API_TOKEN}` },
  });
  assert.equal(admin.body.payload_plaintext_included, false);
  assert.equal(admin.body.events.length, 2);
  assert.equal(admin.body.fraud_flags.length, 1);
  assert.ok(db.prepare('SELECT COUNT(*) AS count FROM audit_events').get().count >= 3);
});
