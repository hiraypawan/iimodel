'use strict';

const crypto = require('node:crypto');
const { canonicalJson, decodePublicKey, encodePublicKey, sha256 } = require('./crypto');

const SCHEMA = 'ii.model-manifest.v1';
const ROLES = new Set(['worker', 'expert', 'phone']);
const FORMATS = new Set(['gguf', 'safetensors', 'mlc', 'other']);

function cleanManifest(manifest) {
  const { signature: _signature, publisher_public_key: _publisherKey, manifest_id: _manifestId, ...payload } = manifest || {};
  return payload;
}

function manifestId(manifest) {
  return sha256(canonicalJson(cleanManifest(manifest)));
}

function validateArtifact(artifact, index) {
  const prefix = `artifacts[${index}]`;
  if (!artifact || typeof artifact !== 'object' || Array.isArray(artifact)) {
    throw new Error(`${prefix} must be an object.`);
  }
  if (typeof artifact.id !== 'string' || !/^[a-zA-Z0-9._-]{1,100}$/.test(artifact.id)) {
    throw new Error(`${prefix}.id is invalid.`);
  }
  if (typeof artifact.sha256 !== 'string' || !/^[a-f0-9]{64}$/.test(artifact.sha256)) {
    throw new Error(`${prefix}.sha256 must contain 64 lowercase hexadecimal characters.`);
  }
  if (!Number.isSafeInteger(artifact.size_bytes) || artifact.size_bytes < 1) {
    throw new Error(`${prefix}.size_bytes must be a positive integer.`);
  }
  if (!FORMATS.has(artifact.format)) throw new Error(`${prefix}.format is unsupported.`);
  if (artifact.expert_id !== undefined
      && (!Number.isInteger(artifact.expert_id) || artifact.expert_id < 0 || artifact.expert_id > 255)) {
    throw new Error(`${prefix}.expert_id must be from 0 to 255.`);
  }
  let url;
  try {
    url = new URL(artifact.url);
  } catch {
    throw new Error(`${prefix}.url must be a valid URL.`);
  }
  const localHttp = url.protocol === 'http:'
    && ['127.0.0.1', 'localhost', '[::1]', '::1'].includes(url.hostname);
  if (url.protocol !== 'https:' && !localHttp) {
    throw new Error(`${prefix}.url must use HTTPS outside local development.`);
  }
  if (url.username || url.password) throw new Error(`${prefix}.url must not contain credentials.`);
}

function validateManifest(manifest) {
  const payload = cleanManifest(manifest);
  if (payload.schema !== SCHEMA) throw new Error(`schema must be ${SCHEMA}.`);
  if (typeof payload.model_id !== 'string' || !/^[a-zA-Z0-9._/-]{2,120}$/.test(payload.model_id)) {
    throw new Error('model_id is invalid.');
  }
  if (typeof payload.version !== 'string' || !/^[a-zA-Z0-9][a-zA-Z0-9.+_-]{0,63}$/.test(payload.version)) {
    throw new Error('version is invalid.');
  }
  if (!ROLES.has(payload.role)) throw new Error('role must be worker, expert, or phone.');
  if (typeof payload.runtime !== 'string' || payload.runtime.length < 2 || payload.runtime.length > 100) {
    throw new Error('runtime is required.');
  }
  if (!payload.license || typeof payload.license !== 'object') throw new Error('license metadata is required.');
  if (typeof payload.license.id !== 'string' || payload.license.id.length < 2 || payload.license.id.length > 100) {
    throw new Error('license.id is required.');
  }
  if (payload.license.redistribution_confirmed !== true) {
    throw new Error('license.redistribution_confirmed must be true before publication.');
  }
  if (!Array.isArray(payload.artifacts) || payload.artifacts.length < 1 || payload.artifacts.length > 512) {
    throw new Error('artifacts must contain 1–512 entries.');
  }
  payload.artifacts.forEach(validateArtifact);
  const artifactIds = new Set(payload.artifacts.map((artifact) => artifact.id));
  if (artifactIds.size !== payload.artifacts.length) throw new Error('Artifact IDs must be unique.');
  const expertIds = payload.artifacts
    .filter((artifact) => artifact.expert_id !== undefined)
    .map((artifact) => artifact.expert_id);
  if (new Set(expertIds).size !== expertIds.length) throw new Error('Expert IDs must be unique in a manifest.');
  if (payload.role === 'expert' && expertIds.length !== payload.artifacts.length) {
    throw new Error('Every artifact in an expert manifest requires expert_id.');
  }
  if (payload.created_at !== undefined && Number.isNaN(Date.parse(payload.created_at))) {
    throw new Error('created_at must be an ISO-8601 timestamp.');
  }
  return {
    ...payload,
    artifacts: payload.artifacts.map((artifact) => ({ ...artifact })),
  };
}

function signManifest(manifest, privateKey, publicKey) {
  const payload = validateManifest(manifest);
  const signature = crypto.sign(null, Buffer.from(canonicalJson(payload)), privateKey);
  return {
    ...payload,
    manifest_id: manifestId(payload),
    publisher_public_key: encodePublicKey(publicKey || crypto.createPublicKey(privateKey)),
    signature: signature.toString('base64'),
  };
}

function verifyManifest(manifest, trustedPublicKeyBase64) {
  const payload = validateManifest(manifest);
  if (manifest.manifest_id !== manifestId(payload)) throw new Error('manifest_id does not match the payload.');
  if (typeof manifest.publisher_public_key !== 'string') throw new Error('publisher_public_key is required.');
  if (trustedPublicKeyBase64 && manifest.publisher_public_key !== trustedPublicKeyBase64) {
    throw new Error('Manifest publisher is not trusted by this installation.');
  }
  const signature = Buffer.from(String(manifest.signature || ''), 'base64');
  if (!signature.length || !crypto.verify(
    null,
    Buffer.from(canonicalJson(payload)),
    decodePublicKey(manifest.publisher_public_key),
    signature,
  )) {
    throw new Error('Manifest signature is invalid.');
  }
  return {
    ...payload,
    manifest_id: manifest.manifest_id,
    publisher_public_key: manifest.publisher_public_key,
    signature: manifest.signature,
  };
}

function artifactForClaim(manifest, claim) {
  if (!manifest || !claim) return null;
  return manifest.artifacts.find((artifact) => artifact.id === claim.artifact_id) || null;
}

module.exports = {
  FORMATS,
  ROLES,
  SCHEMA,
  artifactForClaim,
  cleanManifest,
  manifestId,
  signManifest,
  validateManifest,
  verifyManifest,
};
