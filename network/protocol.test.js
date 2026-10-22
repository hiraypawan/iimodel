'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const {
  deviceIdForPublicKey,
  encodePublicKey,
  generateHostKeyPair,
  signHostPayload,
  verifyHostPayload,
} = require('../protocol/crypto');
const { signManifest, verifyManifest } = require('../protocol/model-manifest');
const { scanSensitiveText } = require('./learning');
const { createEncryptedStore } = require('./security');


test('host identities are derived from Ed25519 keys and signatures bind every claim', () => {
  const host = generateHostKeyPair();
  assert.equal(deviceIdForPublicKey(host.publicKey), host.deviceId);
  const payload = signHostPayload(crypto.createPrivateKey(host.privateKeyPem), 'host:heartbeat', {
    device_id: host.deviceId,
    artifacts: [{ sha256: 'a'.repeat(64), size_bytes: 123 }],
  }, Date.now());
  assert.equal(verifyHostPayload(host.publicKey, 'host:heartbeat', payload), true);
  assert.equal(verifyHostPayload(host.publicKey, 'host:heartbeat', {
    ...payload,
    artifacts: [{ sha256: 'b'.repeat(64), size_bytes: 123 }],
  }), false);
});


test('model manifests reject tampering and untrusted publishers', () => {
  const publisher = crypto.generateKeyPairSync('ed25519');
  const otherPublisher = crypto.generateKeyPairSync('ed25519');
  const manifest = signManifest({
    schema: 'ii.model-manifest.v1',
    model_id: 'ii/protocol-test',
    version: '1.0.0',
    role: 'worker',
    runtime: 'node-llama-cpp@3.5.0',
    license: { id: 'Apache-2.0', redistribution_confirmed: true },
    created_at: '2026-10-22T00:00:00.000Z',
    artifacts: [{
      id: 'worker',
      sha256: 'c'.repeat(64),
      size_bytes: 1024,
      format: 'gguf',
      url: 'https://models.example/worker.gguf',
    }],
  }, publisher.privateKey, publisher.publicKey);
  const trustedKey = encodePublicKey(publisher.publicKey);
  assert.equal(verifyManifest(manifest, trustedKey).manifest_id, manifest.manifest_id);
  assert.throws(
    () => verifyManifest({ ...manifest, runtime: 'tampered-runtime' }, trustedKey),
    /manifest_id does not match/,
  );
  assert.throws(
    () => verifyManifest(manifest, encodePublicKey(otherPublisher.publicKey)),
    /not trusted/,
  );
});


test('encrypted records are authenticated against their row context', () => {
  const store = createEncryptedStore(crypto.randomBytes(32).toString('base64'));
  const ciphertext = store.encrypt('private prompt', 'job:one:prompt');
  assert.equal(store.decrypt(ciphertext, 'job:one:prompt'), 'private prompt');
  assert.throws(() => store.decrypt(ciphertext, 'job:two:prompt'));
});


test('teaching privacy gate detects common direct identifiers and secrets', () => {
  assert.deepEqual(scanSensitiveText('Explain this ordinary Marathi sentence.'), []);
  assert.ok(scanSensitiveText('Contact me at person@example.com').includes('email'));
  assert.ok(scanSensitiveText('My phone is +91 9876543210').includes('indian_phone'));
  assert.ok(scanSensitiveText('-----BEGIN PRIVATE KEY-----').includes('private_key'));
});
