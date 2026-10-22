'use strict';

const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { encodePublicKey } = require('../protocol/crypto');

const outputPath = path.resolve(process.argv[2] || './release-key.pem');
if (fs.existsSync(outputPath)) {
  console.error(`Refusing to overwrite existing key: ${outputPath}`);
  process.exitCode = 1;
} else {
  const { privateKey, publicKey } = crypto.generateKeyPairSync('ed25519');
  fs.writeFileSync(outputPath, privateKey.export({ format: 'pem', type: 'pkcs8' }), { mode: 0o600 });
  console.log(`Private release key written to ${outputPath}`);
  console.log(`II_RELEASE_PUBLIC_KEY=${encodePublicKey(publicKey)}`);
  console.log('Keep the private key offline. Publish only the base64 public key.');
}
