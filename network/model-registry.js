'use strict';

const { artifactForClaim, verifyManifest } = require('../protocol/model-manifest');
const { requireConfiguredBearer } = require('./security');

function manifestRowToObject(row) {
  if (!row) return null;
  return JSON.parse(row.manifest_json);
}

function getManifest(db, manifestId, { activeOnly = true } = {}) {
  if (typeof manifestId !== 'string' || !/^[a-f0-9]{64}$/.test(manifestId)) return null;
  const row = db.prepare(`
    SELECT manifest_json FROM model_manifests
    WHERE manifest_id = ? ${activeOnly ? 'AND active = 1' : ''}
  `).get(manifestId);
  return manifestRowToObject(row);
}

function validateArtifactClaim(db, claim) {
  if (!claim || typeof claim !== 'object' || Array.isArray(claim)) {
    return { ok: false, message: 'artifact must be a manifest-backed object.' };
  }
  const manifest = getManifest(db, claim.manifest_id);
  if (!manifest) return { ok: false, message: 'artifact references an unknown or inactive manifest.' };
  const expected = artifactForClaim(manifest, claim);
  if (!expected) return { ok: false, message: 'artifact_id is not part of the signed manifest.' };
  const exactFields = [
    ['sha256', expected.sha256],
    ['size_bytes', expected.size_bytes],
    ['format', expected.format],
  ];
  if (expected.expert_id !== undefined) exactFields.push(['expert_id', expected.expert_id]);
  for (const [field, expectedValue] of exactFields) {
    if (claim[field] !== expectedValue) {
      return { ok: false, message: `artifact.${field} does not match the signed manifest.` };
    }
  }
  return {
    ok: true,
    manifest,
    artifact: expected,
    claim: {
      manifest_id: manifest.manifest_id,
      artifact_id: expected.id,
      sha256: expected.sha256,
      size_bytes: expected.size_bytes,
      format: expected.format,
      expert_id: expected.expert_id ?? null,
      inference_ready: claim.inference_ready === true,
    },
  };
}

function installModelRoutes(app, db) {
  const requireAdmin = requireConfiguredBearer('II_ADMIN_API_TOKEN', 'admin_api_not_configured');

  app.get(['/models', '/api/models'], (req, res) => {
    const role = typeof req.query.role === 'string' ? req.query.role : null;
    const rows = db.prepare(`
      SELECT manifest_id, model_id, version, role, runtime, active, created_at
      FROM model_manifests
      WHERE active = 1 AND (? IS NULL OR role = ?)
      ORDER BY created_at DESC
    `).all(role, role);
    return res.json({
      ok: true,
      models: rows.map((row) => ({
        manifest_id: row.manifest_id,
        model_id: row.model_id,
        version: row.version,
        role: row.role,
        runtime: row.runtime,
        active: Boolean(row.active),
        created_at: new Date(row.created_at).toISOString(),
      })),
    });
  });

  app.get(['/models/:manifestId', '/api/models/:manifestId'], (req, res) => {
    const manifest = getManifest(db, req.params.manifestId);
    if (!manifest) {
      return res.status(404).json({ error: 'manifest_not_found', message: 'Active model manifest not found.' });
    }
    return res.json({ ok: true, manifest });
  });

  app.post('/admin/models', requireAdmin, (req, res) => {
    const trustedPublisher = process.env.II_RELEASE_PUBLIC_KEY;
    if (!trustedPublisher) {
      return res.status(503).json({
        error: 'release_key_not_configured',
        message: 'Set II_RELEASE_PUBLIC_KEY before publishing signed model manifests.',
      });
    }
    let manifest;
    try {
      manifest = verifyManifest(req.body, trustedPublisher);
    } catch (error) {
      return res.status(400).json({ error: 'invalid_manifest', message: error.message });
    }
    const existing = db.prepare('SELECT manifest_id FROM model_manifests WHERE manifest_id = ?')
      .get(manifest.manifest_id);
    try {
      const publish = db.transaction(() => {
        db.prepare(`
          INSERT INTO model_manifests (
            manifest_id, model_id, version, role, runtime, manifest_json,
            signature, publisher_public_key, active, created_at
          ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
          ON CONFLICT(manifest_id) DO UPDATE SET active = 1
        `).run(
          manifest.manifest_id,
          manifest.model_id,
          manifest.version,
          manifest.role,
          manifest.runtime,
          JSON.stringify(manifest),
          manifest.signature,
          manifest.publisher_public_key,
          Date.parse(manifest.created_at || '') || Date.now(),
        );
        const configuredTarget = Number.parseInt(process.env.II_DEFAULT_REPLICA_TARGET || '2', 10);
        const target = Number.isInteger(configuredTarget)
          ? Math.min(1000, Math.max(1, configuredTarget))
          : 2;
        const minimumRegions = Math.min(target, target > 1 ? 2 : 1);
        const insertTarget = db.prepare(`
          INSERT OR IGNORE INTO replica_targets (
            manifest_id, artifact_id, target_replicas, min_regions, updated_at
          ) VALUES (?, ?, ?, ?, ?)
        `);
        for (const artifact of manifest.artifacts) {
          insertTarget.run(manifest.manifest_id, artifact.id, target, minimumRegions, Date.now());
        }
      });
      publish();
    } catch (error) {
      if (/UNIQUE constraint failed: model_manifests\.model_id/.test(error.message)) {
        return res.status(409).json({
          error: 'model_version_conflict',
          message: 'This model ID and version already identify a different signed manifest.',
        });
      }
      throw error;
    }
    return res.status(existing ? 200 : 201).json({
      ok: true,
      manifest_id: manifest.manifest_id,
      model_id: manifest.model_id,
      version: manifest.version,
      active: true,
    });
  });

  app.post('/admin/models/:manifestId/deactivate', requireAdmin, (req, res) => {
    const result = db.prepare('UPDATE model_manifests SET active = 0 WHERE manifest_id = ?')
      .run(req.params.manifestId);
    if (!result.changes) {
      return res.status(404).json({ error: 'manifest_not_found', message: 'Model manifest not found.' });
    }
    db.prepare(`
      UPDATE jobs
      SET status = 'failed', error = ?, lease_device_id = NULL,
          lease_token_hash = NULL, lease_expires_at = NULL, updated_at = ?
      WHERE manifest_id = ? AND status IN ('queued', 'leased')
    `).run('Model release was deactivated.', Date.now(), req.params.manifestId);
    return res.json({ ok: true, manifest_id: req.params.manifestId, active: false });
  });
}

module.exports = {
  getManifest,
  installModelRoutes,
  manifestRowToObject,
  validateArtifactClaim,
};
