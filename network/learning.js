'use strict';

const crypto = require('node:crypto');
const {
  bearerToken,
  createEncryptedStore,
  randomToken,
  requireConfiguredBearer,
  tokenHash,
  tokenHashMatches,
} = require('./security');

const CONSENT_VERSION = '2026-10-22';

function scanSensitiveText(value) {
  const text = String(value || '');
  const findings = [];
  const checks = [
    ['email', /\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b/i],
    ['indian_phone', /(?:\+?91[\s-]?)?[6-9]\d{9}\b/],
    ['aadhaar_like_number', /\b\d{4}[\s-]?\d{4}[\s-]?\d{4}\b/],
    ['private_key', /-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----/],
    ['api_key', /\b(?:sk|pk|api|key)[-_][A-Za-z0-9_-]{16,}\b/i],
    ['bearer_token', /\bBearer\s+[A-Za-z0-9._~+/-]{12,}=*/i],
  ];
  for (const [kind, pattern] of checks) {
    if (pattern.test(text)) findings.push(kind);
  }
  return [...new Set(findings)];
}

function installLearningRoutes(app, db) {
  const encryptedStore = createEncryptedStore();
  const requireTeachingToken = requireConfiguredBearer('II_TEACHING_API_TOKEN', 'teaching_api_not_configured');
  const requireAdmin = requireConfiguredBearer('II_ADMIN_API_TOKEN', 'admin_api_not_configured');

  function ensureEncryption(res) {
    if (encryptedStore.configured) return true;
    res.status(503).json({
      error: 'teaching_encryption_not_configured',
      message: 'Set II_DATA_ENCRYPTION_KEY before accepting user teachings.',
    });
    return false;
  }

  app.post(['/teachings', '/api/teachings'], requireTeachingToken, (req, res) => {
    if (!ensureEncryption(res)) return undefined;
    const prompt = typeof req.body?.prompt === 'string' ? req.body.prompt.trim() : '';
    const correction = typeof req.body?.correction === 'string' ? req.body.correction.trim() : '';
    const language = typeof req.body?.language === 'string'
      ? req.body.language.trim().slice(0, 35)
      : 'und';
    const category = typeof req.body?.category === 'string'
      ? req.body.category.trim().slice(0, 60)
      : 'general';
    const manifestId = req.body?.manifest_id;

    if (req.body?.consent !== true || req.body?.consent_version !== CONSENT_VERSION) {
      return res.status(400).json({
        error: 'explicit_consent_required',
        message: `Set consent=true and consent_version=${CONSENT_VERSION} after showing the contribution text.`,
      });
    }
    if (!prompt || prompt.length > 4_000 || !correction || correction.length > 8_000) {
      return res.status(400).json({
        error: 'invalid_teaching',
        message: 'prompt must be 1–4000 characters and correction 1–8000 characters.',
      });
    }
    const manifest = db.prepare(`
      SELECT manifest_id FROM model_manifests WHERE manifest_id = ? AND active = 1
    `).get(manifestId);
    if (!manifest) {
      return res.status(400).json({
        error: 'manifest_required',
        message: 'Teachings must identify the exact active signed model release they correct.',
      });
    }

    const findings = scanSensitiveText(`${prompt}\n${correction}`);
    if (findings.length) {
      return res.status(422).json({
        error: 'sensitive_content_detected',
        message: 'Contribution was not stored. Remove personal data or secrets and review it again.',
        findings,
      });
    }

    const id = `teach_${crypto.randomUUID()}`;
    const accessToken = randomToken();
    const now = Date.now();
    db.prepare(`
      INSERT INTO teachings (
        id, manifest_id, prompt_encrypted, correction_encrypted, access_token_hash,
        language, category, consent_version, status, pii_scan_json, created_at, updated_at
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'quarantined', ?, ?, ?)
    `).run(
      id,
      manifestId,
      encryptedStore.encrypt(prompt, `teaching:${id}:prompt`),
      encryptedStore.encrypt(correction, `teaching:${id}:correction`),
      tokenHash(accessToken),
      language || 'und',
      category || 'general',
      CONSENT_VERSION,
      JSON.stringify({ version: 1, findings: [] }),
      now,
      now,
    );
    return res.status(202).json({
      ok: true,
      id,
      status: 'quarantined',
      access_token: accessToken,
      message: 'Stored encrypted for review. It will never be trained or merged automatically.',
    });
  });

  function authorizedTeaching(req, res) {
    const row = db.prepare(`
      SELECT id, manifest_id, language, category, consent_version, status, created_at, updated_at,
             access_token_hash
      FROM teachings WHERE id = ?
    `).get(req.params.id);
    if (!row) {
      res.status(404).json({ error: 'teaching_not_found', message: 'Teaching not found.' });
      return null;
    }
    if (!tokenHashMatches(bearerToken(req), row.access_token_hash)) {
      res.set('WWW-Authenticate', 'Bearer');
      res.status(401).json({ error: 'unauthorized', message: 'The teaching receipt token is required.' });
      return null;
    }
    return row;
  }

  app.get(['/teachings/:id', '/api/teachings/:id'], (req, res) => {
    const row = authorizedTeaching(req, res);
    if (!row) return undefined;
    delete row.access_token_hash;
    return res.json({
      ok: true,
      teaching: {
        ...row,
        created_at: new Date(row.created_at).toISOString(),
        updated_at: new Date(row.updated_at).toISOString(),
      },
    });
  });

  app.delete(['/teachings/:id', '/api/teachings/:id'], (req, res) => {
    const row = authorizedTeaching(req, res);
    if (!row) return undefined;
    db.prepare('DELETE FROM teachings WHERE id = ?').run(row.id);
    return res.json({ ok: true, id: row.id, deleted: true });
  });

  app.get('/admin/teachings', requireAdmin, (_req, res) => {
    const rows = db.prepare(`
      SELECT id, manifest_id, language, category, consent_version, status,
             pii_scan_json, created_at, updated_at
      FROM teachings ORDER BY created_at ASC
    `).all();
    return res.json({
      ok: true,
      content_included: false,
      teachings: rows.map((row) => ({
        ...row,
        pii_scan: JSON.parse(row.pii_scan_json),
        pii_scan_json: undefined,
        created_at: new Date(row.created_at).toISOString(),
        updated_at: new Date(row.updated_at).toISOString(),
      })),
    });
  });
}

module.exports = { CONSENT_VERSION, installLearningRoutes, scanSensitiveText };
