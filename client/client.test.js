'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const fsp = require('node:fs/promises');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');
const { downloadExpert, sha256File } = require('./expert-store');
const { LocalInferenceServer } = require('./inference-server');

const TEN_MIB = 10 * 1024 * 1024;

async function createSafetensorsFixture(filePath) {
  const header = {
    'expert.weight': {
      dtype: 'U8',
      shape: [TEN_MIB],
      data_offsets: [0, TEN_MIB],
    },
    __metadata__: { purpose: 'II v0.2 download test; not inference weights' },
  };
  let headerJson = JSON.stringify(header);
  headerJson += ' '.repeat((8 - (Buffer.byteLength(headerJson) % 8)) % 8);
  const prefix = Buffer.alloc(8);
  prefix.writeBigUInt64LE(BigInt(Buffer.byteLength(headerJson)));

  const handle = await fsp.open(filePath, 'w');
  try {
    await handle.write(prefix);
    await handle.write(Buffer.from(headerJson));
    const chunk = Buffer.alloc(1024 * 1024, 0x2a);
    for (let offset = 0; offset < TEN_MIB; offset += chunk.length) {
      await handle.write(chunk);
    }
    await handle.sync();
  } finally {
    await handle.close();
  }
}

function listen(server) {
  return new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      server.off('error', reject);
      resolve(server.address());
    });
  });
}

test('downloads a real 10 MiB expert artifact and keeps inference fail-closed', async (t) => {
  const temporaryDirectory = await fsp.mkdtemp(path.join(os.tmpdir(), 'ii-client-test-'));
  const fixturePath = path.join(temporaryDirectory, 'fixture.safetensors');
  await createSafetensorsFixture(fixturePath);
  const expectedSha256 = await sha256File(fixturePath);

  const artifactServer = http.createServer((_req, res) => {
    const stats = fs.statSync(fixturePath);
    res.writeHead(200, {
      'Content-Type': 'application/octet-stream',
      'Content-Length': stats.size,
    });
    fs.createReadStream(fixturePath).pipe(res);
  });
  const artifactAddress = await listen(artifactServer);

  const inference = new LocalInferenceServer({ host: '127.0.0.1', port: 0, expertId: 0 });
  await inference.start();
  const inferenceAddress = inference.server.address();

  t.after(async () => {
    await inference.close();
    await new Promise((resolve) => artifactServer.close(resolve));
    await fsp.rm(temporaryDirectory, { recursive: true, force: true });
  });

  const artifact = await downloadExpert({
    homeDirectory: temporaryDirectory,
    expertId: 0,
    sourceUrl: `http://127.0.0.1:${artifactAddress.port}/expert-0.safetensors`,
    expectedSha256,
  });

  assert.equal(artifact.format, 'safetensors');
  assert.equal(artifact.tensorCount, 1);
  assert.equal(artifact.sha256, expectedSha256);
  assert.equal(artifact.downloaded, true);
  assert.ok(artifact.sizeBytes >= TEN_MIB);
  const diskStats = await fsp.stat(artifact.path);
  if (Number.isFinite(diskStats.blocks)) {
    assert.ok(diskStats.blocks * 512 >= TEN_MIB, 'artifact must consume physical disk blocks');
  }

  const reused = await downloadExpert({
    homeDirectory: temporaryDirectory,
    expertId: 0,
    sourceUrl: `http://127.0.0.1:${artifactAddress.port}/expert-0.safetensors`,
    expectedSha256,
  });
  assert.equal(reused.downloaded, false);

  const health = await fetch(`http://127.0.0.1:${inferenceAddress.port}/health`).then((res) => res.json());
  assert.equal(health.loaded, false);

  const response = await fetch(`http://127.0.0.1:${inferenceAddress.port}/infer`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ prompt: 'Run expert zero.' }),
  });
  assert.equal(response.status, 503);
  assert.deepEqual(await response.json(), {
    error: 'expert_not_loaded',
    message: 'expert not loaded yet',
  });

  assert.equal(await inference.loadModel(artifact.path), false);
  assert.match(inference.loadError, /GGUF model/);
});
