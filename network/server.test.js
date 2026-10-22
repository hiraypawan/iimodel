'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fsp = require('node:fs/promises');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');

const temporaryDirectory = require('node:fs').mkdtempSync(path.join(os.tmpdir(), 'ii-network-test-'));
process.env.II_DB_PATH = path.join(temporaryDirectory, 'network.db');
process.env.ADMIN_EXPORT_TOKEN = 'test-admin-token';
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
  const body = await response.json();
  return { response, body };
}

test('400 deterministic assignments provide 20 complete expert copies', () => {
  const replicas = Array(256).fill(0);
  for (let hostNumber = 1; hostNumber <= 400; hostNumber += 1) {
    const experts = assignedExperts(hostNumber);
    assert.equal(experts.length, 13);
    for (const expertId of experts) replicas[expertId] += 1;
  }
  assert.equal(Math.min(...replicas), 20);
  assert.equal(Math.max(...replicas), 21);
});

test('coordinator uses real expert maps, protects exports, limits registration, and creates Dodo checkout', async (t) => {
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

  const firstRegistration = await jsonRequest(`${baseUrl}/register`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      device_id: 'test-host-01',
      country: 'IN',
      endpoint: 'https://host-01.example/infer',
      expert_size_bytes: 10 * 1024 * 1024,
    }),
  });
  assert.equal(firstRegistration.response.status, 201);
  assert.equal(firstRegistration.body.experts_start, 0);
  assert.equal(firstRegistration.body.experts_end, 12);

  const heartbeat = await jsonRequest(`${baseUrl}/heartbeat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      device_id: 'test-host-01',
      experts_start: 0,
      experts_end: 0,
      expert_size_bytes: 10 * 1024 * 1024,
    }),
  });
  assert.equal(heartbeat.response.status, 200);
  assert.equal(heartbeat.body.experts_start, 0);
  assert.equal(heartbeat.body.experts_end, 0);

  for (let number = 2; number <= 10; number += 1) {
    const result = await fetch(`${baseUrl}/register`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ device_id: `test-host-${String(number).padStart(2, '0')}` }),
    });
    assert.ok(result.ok);
  }
  const limited = await jsonRequest(`${baseUrl}/register`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ device_id: 'test-host-11' }),
  });
  assert.equal(limited.response.status, 429);
  assert.equal(limited.body.error, 'registration_rate_limited');

  const route = await jsonRequest(`${baseUrl}/route?expert_ids=0`);
  assert.equal(route.response.status, 200);
  assert.equal(route.body.devices.length, 1);
  assert.equal(route.body.devices[0].device_id, 'test-host-01');
  assert.deepEqual(route.body.devices[0].experts, [0]);
  assert.equal('simulated' in route.body, false);

  const noRoute = await jsonRequest(`${baseUrl}/route?expert_ids=1`);
  assert.deepEqual(noRoute.body.devices, []);

  const promptRoute = await jsonRequest(`${baseUrl}/route?prompt=${encodeURIComponent('Explain distributed AI')}`);
  assert.equal(promptRoute.body.selectionMethod, 'deterministic_prompt_hash');
  assert.equal(promptRoute.body.requestedExperts.length, 5);

  const stats = await jsonRequest(`${baseUrl}/stats`);
  assert.equal(stats.body.onlineHosts, 1);
  assert.equal(stats.body.coveredExperts, 1);
  assert.equal(stats.body.hostedCapacityGb, 0.01);

  const application = await jsonRequest(`${baseUrl}/genesis`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      email: 'host@example.com',
      country: 'India',
      acceptedHostTerms: true,
    }),
  });
  assert.equal(application.response.status, 201);

  const deniedExport = await fetch(`${baseUrl}/admin/genesis`);
  assert.equal(deniedExport.status, 401);
  const allowedExport = await fetch(`${baseUrl}/admin/genesis`, {
    headers: { Authorization: 'Bearer test-admin-token' },
  });
  assert.equal(allowedExport.status, 200);
  assert.match(await allowedExport.text(), /host@example\.com/);

  const checkout = await fetch(`${baseUrl}/api/checkout/monthly`, { redirect: 'manual' });
  assert.equal(checkout.status, 303);
  assert.equal(checkout.headers.get('location'), 'https://checkout.dodopayments.com/cks_test');
  assert.equal(capturedCheckout.method, 'POST');
  assert.equal(capturedCheckout.url, '/checkouts');
  assert.equal(capturedCheckout.authorization, 'Bearer test_dodo_key');
  assert.deepEqual(capturedCheckout.body.product_cart, [
    { product_id: 'pdt_monthly', quantity: 1 },
  ]);
  assert.equal(capturedCheckout.body.return_url, 'https://ii.example/?checkout=success');

  process.env.DODO_YEARLY_URL = 'https://checkout.dodopayments.com/precreated-yearly';
  const fallback = await fetch(`${baseUrl}/api/checkout/yearly`, { redirect: 'manual' });
  assert.equal(fallback.status, 302);
  assert.equal(fallback.headers.get('location'), process.env.DODO_YEARLY_URL);
});
