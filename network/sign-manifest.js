'use strict';

const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { signManifest } = require('../protocol/model-manifest');

function usage() {
  console.error('Usage: node sign-manifest.js INPUT.json OUTPUT.json');
  console.error('Set II_RELEASE_PRIVATE_KEY_PATH to an Ed25519 PKCS8 PEM file.');
}

function main() {
  const [, , inputPath, outputPath] = process.argv;
  const privateKeyPath = process.env.II_RELEASE_PRIVATE_KEY_PATH;
  if (!inputPath || !outputPath || !privateKeyPath) {
    usage();
    process.exitCode = 2;
    return;
  }

  const input = JSON.parse(fs.readFileSync(path.resolve(inputPath), 'utf8'));
  const privateKey = crypto.createPrivateKey(fs.readFileSync(path.resolve(privateKeyPath), 'utf8'));
  if (privateKey.asymmetricKeyType !== 'ed25519') {
    throw new Error('II_RELEASE_PRIVATE_KEY_PATH must contain an Ed25519 private key.');
  }
  const signed = signManifest(input, privateKey);
  fs.writeFileSync(path.resolve(outputPath), `${JSON.stringify(signed, null, 2)}\n`, { mode: 0o600 });
  console.log(`Signed manifest ${signed.manifest_id}`);
  console.log(`Publisher public key: ${signed.publisher_public_key}`);
}

try {
  main();
} catch (error) {
  console.error(`Could not sign manifest: ${error.message}`);
  process.exitCode = 1;
}
