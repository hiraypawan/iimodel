'use strict';

const DEFAULT_TIER_2_COUNTRIES = ['BR', 'ID', 'PH', 'TR', 'ZA', 'MX', 'MY', 'TH', 'VN'];

const apiKey = process.env.DODO_PAYMENTS_API_KEY;
const environment = process.env.DODO_PAYMENTS_ENVIRONMENT || 'test_mode';
const baseUrl = (
  process.env.DODO_PAYMENTS_BASE_URL
  || (environment === 'live_mode'
    ? 'https://live.dodopayments.com'
    : 'https://test.dodopayments.com')
).replace(/\/$/, '');
const dryRun = process.env.DODO_CONFIGURE_DRY_RUN === 'true';
const tier2Countries = [...new Set(
  (process.env.DODO_TIER2_COUNTRIES || DEFAULT_TIER_2_COUNTRIES.join(','))
    .split(',')
    .map((country) => country.trim().toUpperCase())
    .filter((country) => /^[A-Z]{2}$/.test(country) && country !== 'IN'),
)];

const plans = [
  {
    name: 'monthly',
    productId: process.env.DODO_MONTHLY_PRODUCT_ID,
    baseAmount: 2_000,
    interval: 'month',
    indiaAmount: 24_900,
    tier2Amount: 1_000,
  },
  {
    name: 'yearly',
    productId: process.env.DODO_YEARLY_PRODUCT_ID,
    baseAmount: 19_200,
    interval: 'year',
    indiaAmount: 199_900,
    tier2Amount: 9_600,
  },
];

function requireConfiguration() {
  if (!apiKey) throw new Error('DODO_PAYMENTS_API_KEY is required.');
  if (!['test_mode', 'live_mode'].includes(environment) && !process.env.DODO_PAYMENTS_BASE_URL) {
    throw new Error('DODO_PAYMENTS_ENVIRONMENT must be test_mode or live_mode.');
  }
  for (const plan of plans) {
    if (!plan.productId) throw new Error(`DODO_${plan.name.toUpperCase()}_PRODUCT_ID is required.`);
  }
  if (!tier2Countries.length) throw new Error('At least one Tier 2 country must be configured.');
}

async function dodoRequest(route, { method = 'GET', body } = {}) {
  const response = await fetch(`${baseUrl}${route}`, {
    method,
    headers: {
      Authorization: `Bearer ${apiKey}`,
      Accept: 'application/json',
      ...(body ? { 'Content-Type': 'application/json' } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
    signal: AbortSignal.timeout(20_000),
  });
  const text = await response.text();
  let payload = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = text;
    }
  }
  if (!response.ok) {
    const detail = typeof payload === 'string' ? payload.slice(0, 300) : JSON.stringify(payload);
    throw new Error(`Dodo ${method} ${route} returned HTTP ${response.status}: ${detail}`);
  }
  return payload;
}

function assertBasePrice(plan, product) {
  const price = product?.price;
  if (!price || price.currency !== 'USD' || price.price !== plan.baseAmount) {
    throw new Error(
      `${plan.name} product must have base price USD ${plan.baseAmount}; received ${JSON.stringify(price)}.`,
    );
  }
  if (price.type !== 'recurring_price'
    || price.payment_frequency_count !== 1
    || price.payment_frequency_interval !== plan.interval) {
    throw new Error(`${plan.name} product does not have the expected recurring ${plan.interval} interval.`);
  }
}

async function writeRequest(route, options, description) {
  if (dryRun) {
    console.log(`[dry-run] ${description}`);
    return null;
  }
  const result = await dodoRequest(route, options);
  console.log(`[applied] ${description}`);
  return result;
}

async function upsertLocalizedPrice(productId, existingRules, rule) {
  const existing = existingRules.find((item) => item.country_code === rule.country_code);
  if (!existing) {
    await writeRequest(
      `/products/${encodeURIComponent(productId)}/localized-prices`,
      { method: 'POST', body: rule },
      `${rule.country_code}: create ${rule.currency} ${rule.amount}`,
    );
    return;
  }

  if (existing.currency !== rule.currency) {
    await writeRequest(
      `/products/${encodeURIComponent(productId)}/localized-prices/${encodeURIComponent(existing.id)}`,
      { method: 'DELETE' },
      `${rule.country_code}: archive wrong-currency rule ${existing.currency}`,
    );
    await writeRequest(
      `/products/${encodeURIComponent(productId)}/localized-prices`,
      { method: 'POST', body: rule },
      `${rule.country_code}: create ${rule.currency} ${rule.amount}`,
    );
    return;
  }

  if (existing.amount !== rule.amount) {
    await writeRequest(
      `/products/${encodeURIComponent(productId)}/localized-prices/${encodeURIComponent(existing.id)}`,
      { method: 'PATCH', body: { amount: rule.amount } },
      `${rule.country_code}: update amount ${existing.amount} -> ${rule.amount}`,
    );
    return;
  }

  console.log(`[unchanged] ${rule.country_code}: ${rule.currency} ${rule.amount}`);
}

async function configurePlan(plan) {
  console.log(`\nConfiguring II Pro ${plan.name} (${plan.productId})`);
  const productRoute = `/products/${encodeURIComponent(plan.productId)}`;
  const product = await dodoRequest(productRoute);
  assertBasePrice(plan, product);

  if (product.pricing_mode !== 'by_country') {
    await writeRequest(
      productRoute,
      { method: 'PATCH', body: { pricing_mode: 'by_country' } },
      'set pricing_mode=by_country',
    );
  } else {
    console.log('[unchanged] pricing_mode=by_country');
  }

  const list = await dodoRequest(`${productRoute}/localized-prices`);
  const existingRules = Array.isArray(list?.items) ? list.items : [];
  const desiredRules = [
    { country_code: 'IN', currency: 'INR', amount: plan.indiaAmount },
    ...tier2Countries.map((countryCode) => ({
      country_code: countryCode,
      currency: 'USD',
      amount: plan.tier2Amount,
    })),
  ];

  for (const rule of desiredRules) {
    await upsertLocalizedPrice(plan.productId, existingRules, rule);
  }
}

async function main() {
  requireConfiguration();
  console.log(`Dodo environment: ${environment}${dryRun ? ' (dry run)' : ''}`);
  console.log(`Tier 2 countries: ${tier2Countries.join(', ')}`);
  for (const plan of plans) await configurePlan(plan);
  console.log('\nDodo localized pricing configuration complete.');
}

main().catch((error) => {
  console.error(`[dodo-config] ${error.message}`);
  process.exitCode = 1;
});
