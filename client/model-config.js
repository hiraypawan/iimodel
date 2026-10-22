'use strict';

const { verifyManifest } = require('../protocol/model-manifest');

function normalizeCoordinatorUrl(value) {
  return String(value || '').replace(/\/$/, '');
}

async function fetchSignedManifest({ coordinatorUrl, manifestId, trustedPublicKey, timeoutMs = 10_000 }) {
  if (!/^[a-f0-9]{64}$/.test(String(manifestId || ''))) {
    throw new Error('II_MODEL_MANIFEST_ID must contain 64 lowercase hexadecimal characters.');
  }
  if (!trustedPublicKey) {
    throw new Error('II_RELEASE_PUBLIC_KEY is required to verify the model release.');
  }
  const response = await fetch(`${normalizeCoordinatorUrl(coordinatorUrl)}/models/${manifestId}`, {
    signal: AbortSignal.timeout(timeoutMs),
    headers: { Accept: 'application/json' },
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok || !body.manifest) {
    throw new Error(body.message || `Model manifest request returned HTTP ${response.status}.`);
  }
  return verifyManifest(body.manifest, trustedPublicKey);
}

function selectArtifact(manifest, { artifactId, expertId } = {}) {
  let artifact;
  if (artifactId) artifact = manifest.artifacts.find((item) => item.id === artifactId);
  else if (Number.isInteger(expertId)) {
    artifact = manifest.artifacts.find((item) => item.expert_id === expertId);
  } else if (manifest.artifacts.length === 1) [artifact] = manifest.artifacts;

  if (!artifact) {
    throw new Error('Select an artifact with II_ARTIFACT_ID or a matching II_EXPERT_ID.');
  }
  return artifact;
}

function artifactClaim(manifest, artifact, inferenceReady = false) {
  return {
    manifest_id: manifest.manifest_id,
    artifact_id: artifact.id,
    sha256: artifact.sha256,
    size_bytes: artifact.size_bytes,
    format: artifact.format,
    ...(artifact.expert_id !== undefined ? { expert_id: artifact.expert_id } : {}),
    inference_ready: inferenceReady === true,
  };
}

module.exports = { artifactClaim, fetchSignedManifest, selectArtifact };
