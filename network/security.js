'use strict';

const crypto = require('node:crypto');
const {
  deviceIdForPublicKey,
  validateSignedAt,
  verifyHostPayload,
} = require('../protocol/crypto');

function secureTokenMatch(provided, expected) {
  if (!provided || !expected) return false;
  const left = Buffer.from(String(provided));
  const right = Buffer.from(String(expected));
  return left.length === right.length && crypto.timingSafeEqual(left, right);
}

function bearerToken(req) {
  const authorization = req.get('authorization') || '';
  return authorization.startsWith('Bearer ') ? authorization.slice(7) : '';
}

function requireConfiguredBearer(environmentName, unavailableCode = 'service_not_configured') {
  return (req, res, next) => {
    const expected = process.env[environmentName];
    if (!expected || Buffer.byteLength(expected) < 24) {
      return res.status(503).json({
        error: unavailableCode,
        message: `Set ${environmentName} to a random secret of at least 24 bytes before enabling this endpoint.`,
      });
    }
    if (!secureTokenMatch(bearerToken(req), expected)) {
      res.set('WWW-Authenticate', 'Bearer');
      return res.status(401).json({ error: 'unauthorized', message: 'A valid bearer token is required.' });
    }
    return next();
  };
}

function authenticateHost(db, action, body, { consume = false, allowNew = false } = {}) {
  const deviceId = body?.device_id || body?.deviceId;
  const existing = deviceId
    ? db.prepare('SELECT id, public_key, last_signed_at FROM devices WHERE id = ?').get(deviceId)
    : null;
  const revoked = deviceId
    ? db.prepare('SELECT device_id, revoked_at FROM host_revocations WHERE device_id = ?').get(deviceId)
    : null;
  if (revoked) {
    return {
      ok: false,
      status: 403,
      error: 'host_revoked',
      message: 'This host identity has been revoked by the network operator.',
    };
  }
  const allowUnsigned = process.env.II_ALLOW_UNSIGNED_HOSTS === 'true';

  if (!body?.signature || !body?.signed_at) {
    if (allowUnsigned && !existing?.public_key) {
      return { ok: true, legacy: true, deviceId, signedAt: 0, publicKey: null };
    }
    return { ok: false, status: 401, error: 'signed_host_required', message: 'A signed Ed25519 host request is required.' };
  }

  let publicKey = body.public_key || existing?.public_key;
  if (!publicKey) {
    return { ok: false, status: 401, error: 'host_key_missing', message: 'Registration requires public_key.' };
  }

  let derivedId;
  try {
    derivedId = deviceIdForPublicKey(publicKey);
  } catch (error) {
    return { ok: false, status: 400, error: 'invalid_host_key', message: error.message };
  }
  if (deviceId !== derivedId) {
    return {
      ok: false,
      status: 401,
      error: 'host_identity_mismatch',
      message: 'device_id must be derived from the supplied Ed25519 public key.',
    };
  }
  if (!existing && !allowNew) {
    return { ok: false, status: 404, error: 'device_not_registered', message: 'Register this signed host first.' };
  }
  if (existing?.public_key && existing.public_key !== publicKey) {
    return { ok: false, status: 401, error: 'host_key_mismatch', message: 'The host key does not match registration.' };
  }

  const signedAt = Number(body.signed_at);
  const timestampError = validateSignedAt(signedAt, { previous: existing?.last_signed_at || 0 });
  if (timestampError) {
    return { ok: false, status: 401, error: 'invalid_host_timestamp', message: timestampError };
  }
  if (!verifyHostPayload(publicKey, action, body)) {
    return { ok: false, status: 401, error: 'invalid_host_signature', message: 'Host signature verification failed.' };
  }

  if (consume && existing) {
    const update = db.prepare(`
      UPDATE devices SET last_signed_at = ?
      WHERE id = ? AND last_signed_at < ?
    `).run(signedAt, deviceId, signedAt);
    if (update.changes !== 1) {
      return { ok: false, status: 409, error: 'host_replay', message: 'This signed request was already consumed.' };
    }
  }
  return { ok: true, legacy: false, deviceId, signedAt, publicKey, existing };
}

function sendHostAuthError(res, result) {
  if (result.status === 401) res.set('WWW-Authenticate', 'II-Host-Signature');
  return res.status(result.status || 401).json({ error: result.error, message: result.message });
}

function parseEncryptionKey(value = process.env.II_DATA_ENCRYPTION_KEY) {
  if (!value) return null;
  let key;
  if (/^[a-fA-F0-9]{64}$/.test(value)) key = Buffer.from(value, 'hex');
  else {
    try {
      key = Buffer.from(value, 'base64');
    } catch {
      return null;
    }
  }
  return key.length === 32 ? key : null;
}

function createEncryptedStore(keyValue = process.env.II_DATA_ENCRYPTION_KEY) {
  const key = parseEncryptionKey(keyValue);
  return {
    configured: Boolean(key),
    encrypt(value, context = '') {
      if (!key) throw new Error('II_DATA_ENCRYPTION_KEY is not configured as 32 bytes.');
      const iv = crypto.randomBytes(12);
      const cipher = crypto.createCipheriv('aes-256-gcm', key, iv);
      cipher.setAAD(Buffer.from(String(context), 'utf8'));
      const ciphertext = Buffer.concat([cipher.update(String(value), 'utf8'), cipher.final()]);
      const tag = cipher.getAuthTag();
      return `v1.${iv.toString('base64url')}.${tag.toString('base64url')}.${ciphertext.toString('base64url')}`;
    },
    decrypt(value, context = '') {
      if (!key) throw new Error('II_DATA_ENCRYPTION_KEY is not configured as 32 bytes.');
      const [version, ivValue, tagValue, ciphertextValue] = String(value).split('.');
      if (version !== 'v1' || !ivValue || !tagValue || ciphertextValue === undefined) {
        throw new Error('Encrypted value has an unsupported format.');
      }
      const decipher = crypto.createDecipheriv('aes-256-gcm', key, Buffer.from(ivValue, 'base64url'));
      decipher.setAAD(Buffer.from(String(context), 'utf8'));
      decipher.setAuthTag(Buffer.from(tagValue, 'base64url'));
      return Buffer.concat([
        decipher.update(Buffer.from(ciphertextValue, 'base64url')),
        decipher.final(),
      ]).toString('utf8');
    },
  };
}

function randomToken(bytes = 32) {
  return crypto.randomBytes(bytes).toString('base64url');
}

function tokenHash(token) {
  return crypto.createHash('sha256').update(String(token)).digest('hex');
}

function tokenHashMatches(token, expectedHash) {
  const actual = Buffer.from(tokenHash(token));
  const expected = Buffer.from(String(expectedHash || ''));
  return actual.length === expected.length && crypto.timingSafeEqual(actual, expected);
}

module.exports = {
  authenticateHost,
  bearerToken,
  createEncryptedStore,
  parseEncryptionKey,
  randomToken,
  requireConfiguredBearer,
  secureTokenMatch,
  sendHostAuthError,
  tokenHash,
  tokenHashMatches,
};
