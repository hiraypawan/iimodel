'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const fsp = require('node:fs/promises');
const http = require('node:http');
const net = require('node:net');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');
const { fork } = require('node:child_process');
const { loadOrCreateIdentity } = require('./peer-process');
const { signPeerRequest } = require('./auth');

function freePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const { port } = server.address();
      server.close((error) => (error ? reject(error) : resolve(port)));
    });
  });
}

function writeConfig(file, config) {
  fs.writeFileSync(file, `${JSON.stringify(config, null, 2)}\n`);
}

function startPeer(configPath) {
  const child = fork(path.join(__dirname, 'peer-process.js'), [configPath], {
    stdio: ['ignore', 'pipe', 'pipe', 'ipc'],
  });
  const stderr = [];
  child.stderr.on('data', (chunk) => stderr.push(chunk.toString()));
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => {
      child.kill('SIGKILL');
      reject(new Error(`Peer startup timed out: ${stderr.join('')}`));
    }, 10_000);
    child.once('error', reject);
    child.on('message', (message) => {
      if (message?.type === 'ready') {
        clearTimeout(timeout);
        resolve({ child, ready: message, stderr });
      }
    });
    child.once('exit', (code) => {
      clearTimeout(timeout);
      reject(new Error(`Peer exited during startup (${code}): ${stderr.join('')}`));
    });
  });
}

function command(peer, action, payload = {}, timeoutMs = 30_000) {
  const id = crypto.randomUUID();
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => {
      peer.child.off('message', onMessage);
      reject(new Error(`Peer command ${action} timed out.`));
    }, timeoutMs);
    function onMessage(message) {
      if (message?.id !== id) return;
      clearTimeout(timeout);
      peer.child.off('message', onMessage);
      if (message.ok) resolve(message.result);
      else reject(new Error(message.error));
    }
    peer.child.on('message', onMessage);
    peer.child.send({ id, action, ...payload });
  });
}

function rangeServer(bytes) {
  return http.createServer((req, res) => {
    const match = /^bytes=(\d+)-(\d+)$/.exec(req.headers.range || '');
    if (!match) {
      res.writeHead(416);
      res.end();
      return;
    }
    const start = Number(match[1]);
    const end = Math.min(Number(match[2]), bytes.length - 1);
    const chunk = bytes.subarray(start, end + 1);
    res.writeHead(206, {
      'Content-Type': 'application/octet-stream',
      'Content-Length': chunk.length,
      'Content-Range': `bytes ${start}-${end}/${bytes.length}`,
      'Accept-Ranges': 'bytes',
    });
    res.end(chunk);
  });
}

async function listen(server) {
  return new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => resolve(server.address()));
  });
}

test('three signed peer processes discover, relay, resume, enforce limits, reject corruption, and fall back to HTTP', async (t) => {
  const root = await fsp.mkdtemp(path.join(os.tmpdir(), 'ii-p2p-test-'));
  const directories = ['bootstrap', 'relay', 'client'].map((name) => path.join(root, name));
  const [bootstrapIdentity, relayIdentity, clientIdentity] = await Promise.all(
    directories.map((directory) => loadOrCreateIdentity(directory)),
  );
  const [bootstrapPort, relayPort, clientPort] = await Promise.all([freePort(), freePort(), freePort()]);
  const bootstrapUrl = `http://127.0.0.1:${bootstrapPort}`;
  const relayUrl = `http://127.0.0.1:${relayPort}`;
  const artifact = Buffer.alloc(2 * 1024 * 1024 + 321);
  for (let index = 0; index < artifact.length; index += 1) artifact[index] = (index * 31 + 17) % 251;
  const artifactPath = path.join(root, 'artifact.gguf');
  await fsp.writeFile(artifactPath, artifact);
  const artifactHash = crypto.createHash('sha256').update(artifact).digest('hex');

  const configs = [
    {
      stateDirectory: directories[0], host: '127.0.0.1', port: bootstrapPort,
      seedFiles: [{ path: artifactPath, hash: artifactHash }],
      maxChunkBytes: 1024 * 1024,
      interruptRequestNumber: 2,
      interruptOnceAfterBytes: 64 * 1024,
    },
    {
      stateDirectory: directories[1], host: '127.0.0.1', port: relayPort,
      bootstraps: [{ url: bootstrapUrl, publicKey: bootstrapIdentity.publicKey }],
      maxChunkBytes: 1024 * 1024,
    },
    {
      stateDirectory: directories[2], host: '127.0.0.1', port: clientPort,
      bootstraps: [{ url: bootstrapUrl, publicKey: bootstrapIdentity.publicKey }],
      relayUrl,
      maxChunkBytes: 1024 * 1024,
    },
  ];
  const configPaths = configs.map((config, index) => {
    const file = path.join(root, `peer-${index}.json`);
    writeConfig(file, config);
    return file;
  });

  const peers = [];
  const fallbackServers = [];
  t.after(async () => {
    for (const server of fallbackServers) await new Promise((resolve) => server.close(resolve));
    for (const peer of peers) {
      if (peer.child.connected) await command(peer, 'stop').catch(() => peer.child.kill('SIGKILL'));
    }
    await fsp.rm(root, { recursive: true, force: true });
  });

  peers.push(await startPeer(configPaths[0]));
  peers.push(await startPeer(configPaths[1]));
  peers.push(await startPeer(configPaths[2]));
  assert.equal(peers.length, 3);
  assert.equal(peers[0].ready.peer_id, bootstrapIdentity.deviceId);
  assert.equal(peers[1].ready.peer_id, relayIdentity.deviceId);
  assert.equal(peers[2].ready.peer_id, clientIdentity.deviceId);

  const downloaded = await command(peers[2], 'download', {
    options: { hash: artifactHash, size: artifact.length, forceRelay: true },
  }, 45_000);
  assert.match(downloaded.source, /^relay:/);
  assert.equal(await fsp.readFile(downloaded.path).then((bytes) => crypto.createHash('sha256').update(bytes).digest('hex')), artifactHash);
  const clientEvents = await fsp.readFile(path.join(directories[2], 'reputation.jsonl'), 'utf8');
  const relayEvents = await fsp.readFile(path.join(directories[1], 'reputation.jsonl'), 'utf8');
  assert.match(clientEvents, /chunk_failure/);
  assert.match(clientEvents, /content_verified/);
  assert.match(relayEvents, /relayed_chunk/);

  const unsigned = await fetch(`${bootstrapUrl}/v1/peers?hash=${artifactHash}`);
  assert.equal(unsigned.status, 401);
  const route = `/v1/peers?hash=${artifactHash}`;
  const replayHeaders = signPeerRequest(clientIdentity, 'GET', route);
  const firstSigned = await fetch(`${bootstrapUrl}${route}`, { headers: replayHeaders });
  const replayed = await fetch(`${bootstrapUrl}${route}`, { headers: replayHeaders });
  assert.equal(firstSigned.status, 200);
  assert.equal(replayed.status, 401);

  const oversizeRange = `bytes=0-${2 * 1024 * 1024 - 1}`;
  const contentRoute = `/v1/content/${artifactHash}`;
  const bounded = await fetch(`${bootstrapUrl}${contentRoute}`, {
    headers: signPeerRequest(clientIdentity, 'GET', contentRoute, { range: oversizeRange }),
  });
  assert.equal(bounded.status, 416);

  const fallbackBytes = crypto.randomBytes(1024 * 1024 + 99);
  const fallbackHash = crypto.createHash('sha256').update(fallbackBytes).digest('hex');
  const fallback = rangeServer(fallbackBytes);
  fallbackServers.push(fallback);
  const fallbackAddress = await listen(fallback);
  const fallbackResult = await command(peers[2], 'download', {
    options: {
      hash: fallbackHash,
      size: fallbackBytes.length,
      fallbackUrl: `http://127.0.0.1:${fallbackAddress.port}`,
    },
  });
  assert.equal(fallbackResult.source, 'http-fallback');

  const corruptExpected = crypto.createHash('sha256').update(Buffer.alloc(fallbackBytes.length, 7)).digest('hex');
  await assert.rejects(command(peers[2], 'download', {
    options: {
      hash: corruptExpected,
      size: fallbackBytes.length,
      fallbackUrl: `http://127.0.0.1:${fallbackAddress.port}`,
    },
  }), /failed SHA-256 verification/);
  const rejected = await fsp.readdir(path.join(directories[2], 'content', 'rejected'));
  assert.ok(rejected.some((name) => name.startsWith(corruptExpected)));
  const finalEvents = await fsp.readFile(path.join(directories[2], 'reputation.jsonl'), 'utf8');
  assert.match(finalEvents, /content_corruption_rejected/);
});
