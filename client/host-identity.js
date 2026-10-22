'use strict';

const fs = require('node:fs/promises');
const path = require('node:path');
const crypto = require('node:crypto');
const {
  deviceIdForPublicKey,
  encodePublicKey,
  generateHostKeyPair,
  signHostPayload,
} = require('../protocol/crypto');

const IDENTITY_FILE = 'device.json';
const PRIVATE_KEY_FILE = 'device-key.pem';

async function readJson(filePath) {
  return JSON.parse(await fs.readFile(filePath, 'utf8'));
}

async function loadOrCreateHostIdentity(stateDirectory) {
  await fs.mkdir(stateDirectory, { recursive: true, mode: 0o700 });
  const identityPath = path.join(stateDirectory, IDENTITY_FILE);
  const privateKeyPath = path.join(stateDirectory, PRIVATE_KEY_FILE);

  try {
    const [saved, privateKeyPem] = await Promise.all([
      readJson(identityPath),
      fs.readFile(privateKeyPath, 'utf8'),
    ]);
    const privateKey = crypto.createPrivateKey(privateKeyPem);
    if (privateKey.asymmetricKeyType !== 'ed25519') throw new Error('Host key is not Ed25519.');
    const publicKey = encodePublicKey(crypto.createPublicKey(privateKey));
    const deviceId = deviceIdForPublicKey(publicKey);
    if (saved.deviceId !== deviceId || saved.publicKey !== publicKey) {
      throw new Error('Stored host identity does not match its private key.');
    }
    return { ...saved, deviceId, publicKey, privateKeyPem, lastSignedAt: 0 };
  } catch (error) {
    if (!['ENOENT'].includes(error.code) && !(error instanceof SyntaxError)) {
      // A malformed/mismatched identity is retained for operator recovery and a
      // fresh cryptographic identity is generated instead of trusting it.
      const suffix = Date.now();
      await fs.rename(identityPath, `${identityPath}.invalid-${suffix}`).catch(() => {});
      await fs.rename(privateKeyPath, `${privateKeyPath}.invalid-${suffix}`).catch(() => {});
    }
  }

  let legacyDeviceId = null;
  try {
    legacyDeviceId = (await readJson(identityPath)).deviceId || null;
  } catch {
    // No legacy identity to record.
  }

  const generated = generateHostKeyPair();
  const metadata = {
    deviceId: generated.deviceId,
    publicKey: generated.publicKey,
    createdAt: new Date().toISOString(),
    identityVersion: 1,
    ...(legacyDeviceId ? { legacyDeviceId } : {}),
  };
  const temporaryKey = `${privateKeyPath}.tmp`;
  await fs.writeFile(temporaryKey, generated.privateKeyPem, { encoding: 'utf8', mode: 0o600 });
  await fs.rename(temporaryKey, privateKeyPath);
  await fs.chmod(privateKeyPath, 0o600);
  await fs.writeFile(identityPath, `${JSON.stringify(metadata, null, 2)}\n`, {
    encoding: 'utf8',
    mode: 0o600,
  });
  return { ...metadata, privateKeyPem: generated.privateKeyPem, lastSignedAt: 0 };
}

function nextSignedAt(identity) {
  const signedAt = Math.max(Date.now(), Number(identity.lastSignedAt || 0) + 1);
  identity.lastSignedAt = signedAt;
  return signedAt;
}

function signRequest(identity, action, payload, { includePublicKey = false } = {}) {
  if (!identity?.privateKeyPem || !identity.deviceId) throw new Error('Signed host identity is not loaded.');
  const body = {
    ...payload,
    device_id: identity.deviceId,
    ...(includePublicKey ? { public_key: identity.publicKey } : {}),
  };
  return signHostPayload(
    crypto.createPrivateKey(identity.privateKeyPem),
    action,
    body,
    nextSignedAt(identity),
  );
}

function publicIdentity(identity) {
  const { privateKeyPem: _privateKey, lastSignedAt: _lastSignedAt, ...safe } = identity;
  return safe;
}

module.exports = {
  loadOrCreateHostIdentity,
  nextSignedAt,
  publicIdentity,
  signRequest,
};
