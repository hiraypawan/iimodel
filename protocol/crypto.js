'use strict';

const crypto = require('node:crypto');

const HOST_PROTOCOL = 'ii-host-auth-v1';
const MAX_CLOCK_SKEW_MS = 5 * 60 * 1000;
const AUTH_FIELDS = new Set(['signature', 'signed_at', 'public_key']);

function canonicalJson(value) {
  if (value === null || typeof value !== 'object') return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`;
  const entries = Object.entries(value)
    .filter(([, item]) => item !== undefined)
    .sort(([left], [right]) => (left < right ? -1 : left > right ? 1 : 0));
  return `{${entries.map(([key, item]) => `${JSON.stringify(key)}:${canonicalJson(item)}`).join(',')}}`;
}

function sha256(value) {
  return crypto.createHash('sha256').update(value).digest('hex');
}

function decodePublicKey(publicKeyBase64) {
  if (typeof publicKeyBase64 !== 'string' || !/^[A-Za-z0-9+/]+={0,2}$/.test(publicKeyBase64)) {
    throw new Error('Host public_key must be base64-encoded Ed25519 SPKI data.');
  }
  const encoded = Buffer.from(publicKeyBase64, 'base64');
  if (encoded.length < 32 || encoded.length > 128) {
    throw new Error('Host public_key has an invalid length.');
  }
  const key = crypto.createPublicKey({ key: encoded, format: 'der', type: 'spki' });
  if (key.asymmetricKeyType !== 'ed25519') {
    throw new Error('Host public_key must be an Ed25519 key.');
  }
  return key;
}

function encodePublicKey(publicKey) {
  const key = typeof publicKey === 'string' || Buffer.isBuffer(publicKey)
    ? crypto.createPublicKey(publicKey)
    : publicKey;
  if (key.asymmetricKeyType !== 'ed25519') throw new Error('Expected an Ed25519 public key.');
  return key.export({ format: 'der', type: 'spki' }).toString('base64');
}

function deviceIdForPublicKey(publicKeyBase64) {
  const key = decodePublicKey(publicKeyBase64);
  const encoded = key.export({ format: 'der', type: 'spki' });
  return `ii-${sha256(encoded).slice(0, 40)}`;
}

function unsignedPayload(payload) {
  return Object.fromEntries(
    Object.entries(payload || {}).filter(([key]) => !AUTH_FIELDS.has(key)),
  );
}

function hostMessage(action, signedAt, payload) {
  if (typeof action !== 'string' || !/^[a-z][a-z0-9:_-]{1,63}$/.test(action)) {
    throw new Error('Invalid host authentication action.');
  }
  if (!Number.isSafeInteger(signedAt) || signedAt < 1) {
    throw new Error('signed_at must be a positive integer timestamp.');
  }
  return Buffer.from(
    `${HOST_PROTOCOL}\n${action}\n${signedAt}\n${canonicalJson(unsignedPayload(payload))}`,
    'utf8',
  );
}

function signHostPayload(privateKey, action, payload, signedAt = Date.now()) {
  const signature = crypto.sign(null, hostMessage(action, signedAt, payload), privateKey);
  return {
    ...payload,
    signed_at: signedAt,
    signature: signature.toString('base64'),
  };
}

function verifyHostPayload(publicKeyBase64, action, payload) {
  const signedAt = Number(payload?.signed_at);
  const signature = Buffer.from(String(payload?.signature || ''), 'base64');
  if (!signature.length || signature.length > 128) return false;
  try {
    return crypto.verify(
      null,
      hostMessage(action, signedAt, payload),
      decodePublicKey(publicKeyBase64),
      signature,
    );
  } catch {
    return false;
  }
}

function validateSignedAt(signedAt, { now = Date.now(), previous = 0 } = {}) {
  if (!Number.isSafeInteger(signedAt)) return 'signed_at must be an integer timestamp.';
  if (Math.abs(now - signedAt) > MAX_CLOCK_SKEW_MS) {
    return 'Host signature timestamp is outside the five-minute clock window.';
  }
  if (signedAt <= previous) return 'Host signature was already used or is older than the last request.';
  return null;
}

function generateHostKeyPair() {
  const { publicKey, privateKey } = crypto.generateKeyPairSync('ed25519');
  const publicKeyBase64 = encodePublicKey(publicKey);
  return {
    deviceId: deviceIdForPublicKey(publicKeyBase64),
    publicKey: publicKeyBase64,
    privateKeyPem: privateKey.export({ format: 'pem', type: 'pkcs8' }),
  };
}

module.exports = {
  MAX_CLOCK_SKEW_MS,
  canonicalJson,
  decodePublicKey,
  deviceIdForPublicKey,
  encodePublicKey,
  generateHostKeyPair,
  hostMessage,
  sha256,
  signHostPayload,
  unsignedPayload,
  validateSignedAt,
  verifyHostPayload,
};
