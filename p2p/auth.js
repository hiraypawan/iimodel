'use strict';

const crypto = require('node:crypto');
const {
  canonicalJson,
  decodePublicKey,
  deviceIdForPublicKey,
  encodePublicKey,
  generateHostKeyPair,
} = require('../protocol/crypto');

const PEER_PROTOCOL = 'ii-p2p-http-v1';
const CLOCK_SKEW_MS = 5 * 60 * 1_000;

function sha256(value) {
  return crypto.createHash('sha256').update(value).digest('hex');
}

function message({ method, path, peerId, timestamp, nonce, range = '', bodyHash }) {
  return Buffer.from(`${PEER_PROTOCOL}\n${method.toUpperCase()}\n${path}\n${peerId}\n${timestamp}\n${nonce}\n${range}\n${bodyHash}`, 'utf8');
}

function signPeerRequest(identity, method, path, { range = '', body = Buffer.alloc(0), now = Date.now() } = {}) {
  const nonce = crypto.randomBytes(18).toString('base64url');
  const bodyBuffer = Buffer.isBuffer(body) ? body : Buffer.from(body);
  const fields = {
    method,
    path,
    peerId: identity.deviceId,
    timestamp: now,
    nonce,
    range,
    bodyHash: sha256(bodyBuffer),
  };
  const privateKey = crypto.createPrivateKey(identity.privateKeyPem);
  return {
    'x-ii-peer-id': identity.deviceId,
    'x-ii-peer-public-key': identity.publicKey,
    'x-ii-peer-timestamp': String(now),
    'x-ii-peer-nonce': nonce,
    'x-ii-peer-signature': crypto.sign(null, message(fields), privateKey).toString('base64'),
    ...(range ? { range } : {}),
  };
}

class ReplayWindow {
  constructor({ maximum = 20_000, ttlMs = CLOCK_SKEW_MS } = {}) {
    this.maximum = maximum;
    this.ttlMs = ttlMs;
    this.nonces = new Map();
  }

  consume(peerId, nonce, now = Date.now()) {
    const key = `${peerId}:${nonce}`;
    if (this.nonces.has(key)) return false;
    this.nonces.set(key, now);
    if (this.nonces.size > this.maximum) {
      for (const [candidate, createdAt] of this.nonces) {
        if (createdAt < now - this.ttlMs || this.nonces.size > this.maximum) this.nonces.delete(candidate);
      }
    }
    return true;
  }
}

function verifyPeerRequest(req, body, replayWindow, now = Date.now()) {
  const peerId = String(req.headers['x-ii-peer-id'] || '');
  const publicKey = String(req.headers['x-ii-peer-public-key'] || '');
  const timestamp = Number(req.headers['x-ii-peer-timestamp']);
  const nonce = String(req.headers['x-ii-peer-nonce'] || '');
  const encodedSignature = String(req.headers['x-ii-peer-signature'] || '');
  if (!peerId || !publicKey || !Number.isSafeInteger(timestamp) || !nonce || !encodedSignature) {
    return { ok: false, error: 'missing_peer_authentication' };
  }
  if (Math.abs(now - timestamp) > CLOCK_SKEW_MS) return { ok: false, error: 'peer_timestamp_outside_window' };
  if (!/^[A-Za-z0-9_-]{20,80}$/.test(nonce)) return { ok: false, error: 'invalid_peer_nonce' };
  let derived;
  try { derived = deviceIdForPublicKey(publicKey); } catch { return { ok: false, error: 'invalid_peer_public_key' }; }
  if (derived !== peerId) return { ok: false, error: 'peer_identity_mismatch' };
  const bodyBuffer = Buffer.isBuffer(body) ? body : Buffer.from(body || '');
  const fields = {
    method: req.method,
    path: req.url,
    peerId,
    timestamp,
    nonce,
    range: String(req.headers.range || ''),
    bodyHash: sha256(bodyBuffer),
  };
  let valid = false;
  try {
    const signature = Buffer.from(encodedSignature, 'base64');
    valid = signature.length > 0 && signature.length <= 128
      && crypto.verify(null, message(fields), decodePublicKey(publicKey), signature);
  } catch {
    valid = false;
  }
  if (!valid) return { ok: false, error: 'invalid_peer_signature' };
  if (!replayWindow.consume(peerId, nonce, now)) return { ok: false, error: 'replayed_peer_request' };
  return { ok: true, peerId, publicKey, timestamp };
}

function signDirectory(identity, payload) {
  const document = { schema: 'ii.peer-directory.v1', ...payload };
  const privateKey = crypto.createPrivateKey(identity.privateKeyPem);
  return {
    document,
    public_key: identity.publicKey,
    signature: crypto.sign(null, Buffer.from(canonicalJson(document)), privateKey).toString('base64'),
  };
}

function verifyDirectory(envelope, expectedPublicKey) {
  if (!envelope?.document || envelope.public_key !== expectedPublicKey) return false;
  try {
    return crypto.verify(
      null,
      Buffer.from(canonicalJson(envelope.document)),
      decodePublicKey(expectedPublicKey),
      Buffer.from(envelope.signature || '', 'base64'),
    );
  } catch {
    return false;
  }
}

function generatePeerIdentity() {
  return generateHostKeyPair();
}

function publicPeerIdentity(identity) {
  const key = crypto.createPrivateKey(identity.privateKeyPem);
  const publicKey = identity.publicKey || encodePublicKey(crypto.createPublicKey(key));
  return { deviceId: deviceIdForPublicKey(publicKey), publicKey };
}

module.exports = {
  CLOCK_SKEW_MS,
  ReplayWindow,
  generatePeerIdentity,
  message,
  publicPeerIdentity,
  sha256,
  signDirectory,
  signPeerRequest,
  verifyDirectory,
  verifyPeerRequest,
};
