'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const path = require('node:path');
const { execFile } = require('node:child_process');
const { promisify } = require('node:util');

const execFileAsync = promisify(execFile);

function listen(server) {
  return new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      server.off('error', reject);
      resolve(server.address());
    });
  });
}

test('Dodo provisioning applies by_country mode and exact smallest-unit prices', async (t) => {
  const writes = [];
  const server = http.createServer(async (req, res) => {
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    const body = chunks.length ? JSON.parse(Buffer.concat(chunks).toString('utf8')) : null;
    if (req.method !== 'GET') writes.push({ method: req.method, url: req.url, body });

    if (req.method === 'GET' && req.url.endsWith('/localized-prices')) {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      return res.end(JSON.stringify({ items: [] }));
    }
    if (req.method === 'GET' && req.url === '/products/pdt_monthly') {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      return res.end(JSON.stringify({
        product_id: 'pdt_monthly',
        pricing_mode: null,
        price: {
          type: 'recurring_price',
          currency: 'USD',
          price: 2_000,
          payment_frequency_count: 1,
          payment_frequency_interval: 'month',
        },
      }));
    }
    if (req.method === 'GET' && req.url === '/products/pdt_yearly') {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      return res.end(JSON.stringify({
        product_id: 'pdt_yearly',
        pricing_mode: null,
        price: {
          type: 'recurring_price',
          currency: 'USD',
          price: 19_200,
          payment_frequency_count: 1,
          payment_frequency_interval: 'year',
        },
      }));
    }
    if (req.method === 'PATCH') {
      res.writeHead(204);
      return res.end();
    }
    if (req.method === 'POST' && req.url.endsWith('/localized-prices')) {
      res.writeHead(201, { 'Content-Type': 'application/json' });
      return res.end(JSON.stringify({ id: `lcp_${writes.length}`, ...body }));
    }
    res.writeHead(404, { 'Content-Type': 'application/json' });
    return res.end(JSON.stringify({ error: 'not_found' }));
  });
  const address = await listen(server);
  t.after(() => new Promise((resolve) => server.close(resolve)));

  const { stdout } = await execFileAsync(process.execPath, [
    path.join(__dirname, 'configure-dodo.js'),
  ], {
    env: {
      ...process.env,
      DODO_PAYMENTS_API_KEY: 'test_key',
      DODO_PAYMENTS_ENVIRONMENT: 'test_mode',
      DODO_PAYMENTS_BASE_URL: `http://127.0.0.1:${address.port}`,
      DODO_MONTHLY_PRODUCT_ID: 'pdt_monthly',
      DODO_YEARLY_PRODUCT_ID: 'pdt_yearly',
      DODO_TIER2_COUNTRIES: 'BR,ID',
    },
  });

  assert.match(stdout, /configuration complete/);
  const modeWrites = writes.filter((item) => item.method === 'PATCH');
  assert.deepEqual(modeWrites, [
    { method: 'PATCH', url: '/products/pdt_monthly', body: { pricing_mode: 'by_country' } },
    { method: 'PATCH', url: '/products/pdt_yearly', body: { pricing_mode: 'by_country' } },
  ]);

  const localizedWrites = writes.filter((item) => item.method === 'POST').map((item) => ({
    url: item.url,
    body: item.body,
  }));
  assert.deepEqual(localizedWrites, [
    { url: '/products/pdt_monthly/localized-prices', body: { country_code: 'IN', currency: 'INR', amount: 24_900 } },
    { url: '/products/pdt_monthly/localized-prices', body: { country_code: 'BR', currency: 'USD', amount: 1_000 } },
    { url: '/products/pdt_monthly/localized-prices', body: { country_code: 'ID', currency: 'USD', amount: 1_000 } },
    { url: '/products/pdt_yearly/localized-prices', body: { country_code: 'IN', currency: 'INR', amount: 199_900 } },
    { url: '/products/pdt_yearly/localized-prices', body: { country_code: 'BR', currency: 'USD', amount: 9_600 } },
    { url: '/products/pdt_yearly/localized-prices', body: { country_code: 'ID', currency: 'USD', amount: 9_600 } },
  ]);
});
