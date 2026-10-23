'use strict';

const fsp = require('node:fs/promises');
const path = require('node:path');
const crypto = require('node:crypto');
const { generatePeerIdentity } = require('./auth');
const { PeerNode } = require('./node');

async function loadOrCreateIdentity(stateDirectory) {
  const identityPath = path.join(stateDirectory, 'peer-identity.json');
  try {
    const identity = JSON.parse(await fsp.readFile(identityPath, 'utf8'));
    const key = crypto.createPrivateKey(identity.privateKeyPem);
    if (key.asymmetricKeyType !== 'ed25519') throw new Error('not Ed25519');
    return identity;
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
  }
  const identity = generatePeerIdentity();
  await fsp.mkdir(stateDirectory, { recursive: true, mode: 0o700 });
  await fsp.writeFile(identityPath, `${JSON.stringify(identity)}\n`, { mode: 0o600 });
  return identity;
}

async function main() {
  const configPath = process.argv[2];
  if (!configPath) throw new Error('Usage: node peer-process.js /path/to/config.json');
  const config = JSON.parse(await fsp.readFile(configPath, 'utf8'));
  const stateDirectory = path.resolve(config.stateDirectory);
  const identity = await loadOrCreateIdentity(stateDirectory);
  const node = new PeerNode({ ...config, identity, stateDirectory });
  await node.init();
  for (const seed of config.seedFiles || []) {
    await node.store.importFile(path.resolve(seed.path), seed.hash || null);
  }
  await node.start();
  const ready = {
    type: 'ready',
    pid: process.pid,
    endpoint: node.endpoint,
    peer_id: identity.deviceId,
    public_key: identity.publicKey,
  };
  if (process.send) process.send(ready);
  else console.log(JSON.stringify(ready));

  process.on('message', async (message) => {
    if (!message?.id) return;
    try {
      let result;
      if (message.action === 'download') result = await node.download(message.options);
      else if (message.action === 'import') {
        result = await node.store.importFile(path.resolve(message.path), message.hash || null);
        await node.announce();
      } else if (message.action === 'announce') {
        await node.announce();
        result = { announced: true };
      } else if (message.action === 'identity') result = ready;
      else if (message.action === 'stop') {
        await node.close();
        result = { stopped: true };
        process.send?.({ id: message.id, ok: true, result });
        process.exit(0);
        return;
      } else throw new Error('Unknown peer process action.');
      process.send?.({ id: message.id, ok: true, result });
    } catch (error) {
      process.send?.({ id: message.id, ok: false, error: error.message });
    }
  });

  const shutdown = async () => {
    await node.close().catch(() => {});
    process.exit(0);
  };
  process.on('SIGINT', shutdown);
  process.on('SIGTERM', shutdown);
}

if (require.main === module) {
  main().catch((error) => {
    console.error(`[ii-p2p] ${error.stack || error.message}`);
    process.exit(1);
  });
}

module.exports = { loadOrCreateIdentity };
