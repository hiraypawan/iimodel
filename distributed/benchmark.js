'use strict';

const { DistributedActivationReference } = require('./reference');

function parseNodes(value) {
  const urls = String(value || '').split(',').map((item) => item.trim()).filter(Boolean);
  if (urls.length !== 2) throw new Error('II_ACTIVATION_NODES must contain exactly two comma-separated worker URLs.');
  return [
    { url: urls[0], layerStart: 0, layerEnd: 4 },
    { url: urls[1], layerStart: 4, layerEnd: 8 },
  ];
}

async function main() {
  const width = Number.parseInt(process.env.II_ACTIVATION_WIDTH || '4096', 10);
  const runs = Number.parseInt(process.env.II_ACTIVATION_RUNS || '10', 10);
  if (!Number.isInteger(width) || width < 1 || width > 2_000_000) throw new Error('II_ACTIVATION_WIDTH is invalid.');
  const values = new Float32Array(width);
  for (let index = 0; index < width; index += 1) values[index] = Math.sin(index * 0.017) * 0.5;
  const reference = new DistributedActivationReference({
    nodes: parseNodes(process.env.II_ACTIVATION_NODES),
    secret: process.env.II_ACTIVATION_SECRET,
    timeoutMs: Number.parseInt(process.env.II_ACTIVATION_TIMEOUT_MS || '2000', 10),
  });
  const result = await reference.benchmark(values, { warmups: 1, runs });
  process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
}

if (require.main === module) {
  main().catch((error) => {
    console.error(`[activation-benchmark] ${error.message}`);
    process.exit(1);
  });
}

module.exports = { parseNodes };
