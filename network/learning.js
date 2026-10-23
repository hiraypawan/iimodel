'use strict';

const crypto = require('node:crypto');
const {
  canonicalJson,
  encodePublicKey,
} = require('../protocol/crypto');
const {
  bearerToken,
  createEncryptedStore,
  randomToken,
  requireConfiguredBearer,
  tokenHash,
  tokenHashMatches,
} = require('./security');
const { privacyScan, scanSensitiveText } = require('./privacy');

const CONSENT_VERSION = '2026-10-22';
const MAX_DATASET_ITEMS = 10_000;

function cleanText(value, maximum, fallback = '') {
  if (typeof value !== 'string') return fallback;
  return value.trim().replace(/[\u0000-\u001f\u007f]/g, '').slice(0, maximum) || fallback;
}

function safeJson(value, maximumBytes = 32_000) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  try {
    const encoded = JSON.stringify(value);
    if (Buffer.byteLength(encoded) > maximumBytes) return null;
    return JSON.parse(encoded);
  } catch {
    return null;
  }
}

function hash(value) {
  return crypto.createHash('sha256').update(value).digest('hex');
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

  function audit(action, targetType, targetId, details = {}) {
    db.prepare(`
      INSERT INTO audit_events (
        actor_type, actor_id, action, target_type, target_id, details_json, created_at
      ) VALUES ('admin', ?, ?, ?, ?, ?, ?)
    `).run(
      cleanText(details.actor, 120, 'configured-admin-token'),
      action,
      targetType,
      targetId,
      JSON.stringify({ ...details, actor: undefined }),
      Date.now(),
    );
  }

  function decryptTeaching(row) {
    return {
      prompt: encryptedStore.decrypt(row.prompt_encrypted, `teaching:${row.id}:prompt`),
      correction: encryptedStore.decrypt(row.correction_encrypted, `teaching:${row.id}:correction`),
    };
  }

  app.post(['/teachings', '/api/teachings'], requireTeachingToken, (req, res) => {
    if (!ensureEncryption(res)) return undefined;
    const prompt = typeof req.body?.prompt === 'string' ? req.body.prompt.trim() : '';
    const correction = typeof req.body?.correction === 'string' ? req.body.correction.trim() : '';
    const language = cleanText(req.body?.language, 35, 'und');
    const category = cleanText(req.body?.category, 60, 'general');
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

    const scan = privacyScan(`${prompt}\n${correction}`);
    if (!scan.passed) {
      return res.status(422).json({
        error: 'sensitive_content_detected',
        message: 'Contribution was not stored. Remove personal data or secrets and review it again.',
        findings: scan.findings,
        scanner: scan.schema,
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
      language,
      category,
      CONSENT_VERSION,
      JSON.stringify(scan),
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

  const withdrawTeaching = db.transaction((row, actor, now) => {
    const affectedDatasets = db.prepare(`
      SELECT dv.id, dv.status
      FROM dataset_items di JOIN dataset_versions dv ON dv.id = di.dataset_id
      WHERE di.teaching_id = ?
    `).all(row.id);
    const affectedReleases = db.prepare(`
      SELECT ar.id FROM adapter_releases ar
      JOIN training_runs tr ON tr.id = ar.training_run_id
      JOIN dataset_items di ON di.dataset_id = tr.dataset_id
      WHERE di.teaching_id = ? AND ar.status IN ('staged', 'active')
    `).all(row.id);
    const withdrawalId = `withdraw_${crypto.randomUUID()}`;
    const affected = {
      datasets: affectedDatasets,
      adapter_releases: affectedReleases.map((item) => item.id),
      limitation: affectedReleases.length
        ? 'Published model weights cannot be selectively untrained here; affected releases were withdrawn from rollout.'
        : null,
    };
    db.prepare(`
      INSERT INTO withdrawal_requests (
        id, teaching_id, status, affected_json, requested_at, completed_at
      ) VALUES (?, ?, ?, ?, ?, ?)
    `).run(
      withdrawalId,
      row.id,
      affectedDatasets.some((item) => item.status === 'sealed')
        ? 'blocked_by_sealed_dataset'
        : 'completed',
      JSON.stringify(affected),
      now,
      now,
    );
    db.prepare(`
      UPDATE adapter_releases SET status = 'withdrawn', rollout_percent = 0, updated_at = ?
      WHERE id IN (
        SELECT ar.id FROM adapter_releases ar
        JOIN training_runs tr ON tr.id = ar.training_run_id
        JOIN dataset_items di ON di.dataset_id = tr.dataset_id
        WHERE di.teaching_id = ? AND ar.status IN ('staged', 'active')
      )
    `).run(now, row.id);
    db.prepare(`
      UPDATE teachings
      SET status = 'deleted', prompt_encrypted = ?, correction_encrypted = ?,
          access_token_hash = ?, updated_at = ?
      WHERE id = ?
    `).run(
      encryptedStore.encrypt('[withdrawn]', `teaching:${row.id}:prompt`),
      encryptedStore.encrypt('[withdrawn]', `teaching:${row.id}:correction`),
      tokenHash(randomToken()),
      now,
      row.id,
    );
    audit('teaching.withdraw', 'teaching', row.id, { actor, withdrawal_id: withdrawalId });
    return { withdrawalId, affected };
  });

  app.delete(['/teachings/:id', '/api/teachings/:id'], (req, res) => {
    if (!ensureEncryption(res)) return undefined;
    const row = authorizedTeaching(req, res);
    if (!row) return undefined;
    if (row.status === 'deleted') return res.json({ ok: true, id: row.id, deleted: true });
    const result = withdrawTeaching(row, 'contributor', Date.now());
    return res.json({
      ok: true,
      id: row.id,
      deleted: true,
      withdrawal_id: result.withdrawalId,
      affected: result.affected,
    });
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

  app.get('/admin/teachings/:id/content', requireAdmin, (req, res) => {
    if (!ensureEncryption(res)) return undefined;
    const row = db.prepare('SELECT * FROM teachings WHERE id = ?').get(req.params.id);
    if (!row) return res.status(404).json({ error: 'teaching_not_found' });
    const content = decryptTeaching(row);
    const scan = privacyScan(`${content.prompt}\n${content.correction}`);
    audit('teaching.content_view', 'teaching', row.id, {
      actor: req.get('x-reviewer-id'),
      scan_passed: scan.passed,
    });
    return res.json({
      ok: true,
      teaching: {
        id: row.id,
        manifest_id: row.manifest_id,
        language: row.language,
        category: row.category,
        prompt: content.prompt,
        correction: content.correction,
        privacy_scan: scan,
      },
    });
  });

  app.post('/admin/teachings/:id/review', requireAdmin, (req, res) => {
    if (!ensureEncryption(res)) return undefined;
    const row = db.prepare('SELECT * FROM teachings WHERE id = ?').get(req.params.id);
    if (!row || row.status === 'deleted') return res.status(404).json({ error: 'teaching_not_found' });
    const decision = req.body?.decision;
    if (!['approved', 'rejected', 'needs_changes'].includes(decision)) {
      return res.status(400).json({ error: 'invalid_review_decision' });
    }
    const reviewerId = cleanText(req.body?.reviewer_id || req.get('x-reviewer-id'), 120);
    if (!reviewerId) return res.status(400).json({ error: 'reviewer_required' });
    const content = decryptTeaching(row);
    const scan = privacyScan(`${content.prompt}\n${content.correction}`);
    if (decision === 'approved' && !scan.passed) {
      return res.status(422).json({ error: 'privacy_gate_failed', findings: scan.findings });
    }
    const notes = cleanText(req.body?.notes, 4_000);
    const id = `review_${crypto.randomUUID()}`;
    const now = Date.now();
    db.transaction(() => {
      db.prepare(`
        INSERT INTO teaching_reviews (
          id, teaching_id, reviewer_id, decision, notes_encrypted, scan_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
      `).run(
        id,
        row.id,
        reviewerId,
        decision,
        notes ? encryptedStore.encrypt(notes, `review:${id}:notes`) : null,
        JSON.stringify(scan),
        now,
      );
      db.prepare('UPDATE teachings SET status = ?, pii_scan_json = ?, updated_at = ? WHERE id = ?')
        .run(decision === 'approved' ? 'approved' : decision === 'rejected' ? 'rejected' : 'quarantined', JSON.stringify(scan), now, row.id);
      audit(`teaching.${decision}`, 'teaching', row.id, { actor: reviewerId, review_id: id });
    })();
    return res.json({ ok: true, id: row.id, review_id: id, status: decision });
  });

  app.post('/admin/datasets', requireAdmin, (req, res) => {
    if (!ensureEncryption(res)) return undefined;
    const requested = Array.isArray(req.body?.teaching_ids)
      ? [...new Set(req.body.teaching_ids.filter((item) => typeof item === 'string'))]
      : [];
    const creator = cleanText(req.body?.created_by || req.get('x-reviewer-id'), 120);
    if (!requested.length || requested.length > MAX_DATASET_ITEMS || !creator) {
      return res.status(400).json({ error: 'invalid_dataset_request' });
    }
    const placeholders = requested.map(() => '?').join(',');
    const rows = db.prepare(`
      SELECT * FROM teachings WHERE id IN (${placeholders}) ORDER BY id
    `).all(...requested);
    if (rows.length !== requested.length || rows.some((row) => row.status !== 'approved')) {
      return res.status(409).json({
        error: 'unapproved_dataset_item',
        message: 'Every item must exist, pass privacy scanning, and have human approval.',
      });
    }
    const manifestIds = [...new Set(rows.map((row) => row.manifest_id))];
    if (manifestIds.length !== 1) {
      return res.status(409).json({ error: 'mixed_manifest_dataset' });
    }
    const id = `dataset_${crypto.randomUUID()}`;
    const provenance = [];
    const items = [];
    const uniqueContent = new Set();
    for (const row of rows) {
      const content = decryptTeaching(row);
      const scan = privacyScan(`${content.prompt}\n${content.correction}`);
      if (!scan.passed) return res.status(422).json({ error: 'privacy_gate_failed', teaching_id: row.id });
      const reviews = db.prepare(`
        SELECT id, reviewer_id, decision, created_at FROM teaching_reviews
        WHERE teaching_id = ? AND decision = 'approved' ORDER BY created_at
      `).all(row.id);
      const contentHash = hash(canonicalJson({
        prompt: content.prompt,
        correction: content.correction,
        consent_version: row.consent_version,
      }));
      const itemHash = hash(canonicalJson({
        teaching_id: row.id,
        content_hash: contentHash,
      }));
      if (uniqueContent.has(contentHash)) {
        return res.status(409).json({
          error: 'duplicate_dataset_content',
          message: 'Two approved contributions contain the same prompt/correction/consent payload.',
        });
      }
      uniqueContent.add(contentHash);
      items.push({ teachingId: row.id, itemHash });
      provenance.push({
        teaching_id: row.id,
        consent_version: row.consent_version,
        source_manifest_id: row.manifest_id,
        approval_reviews: reviews,
        content_hash: contentHash,
        item_hash: itemHash,
      });
    }
    const now = Date.now();
    db.transaction(() => {
      db.prepare(`
        INSERT INTO dataset_versions (
          id, manifest_id, status, item_count, provenance_json, created_by, created_at
        ) VALUES (?, ?, 'draft', ?, ?, ?, ?)
      `).run(id, manifestIds[0], items.length, JSON.stringify(provenance), creator, now);
      const insert = db.prepare(`
        INSERT INTO dataset_items (dataset_id, teaching_id, item_hash) VALUES (?, ?, ?)
      `);
      for (const item of items) insert.run(id, item.teachingId, item.itemHash);
      audit('dataset.create', 'dataset', id, { actor: creator, item_count: items.length });
    })();
    return res.status(201).json({ ok: true, id, status: 'draft', item_count: items.length });
  });

  app.post('/admin/datasets/:id/seal', requireAdmin, (req, res) => {
    const dataset = db.prepare('SELECT * FROM dataset_versions WHERE id = ?').get(req.params.id);
    if (!dataset) return res.status(404).json({ error: 'dataset_not_found' });
    if (dataset.status !== 'draft') return res.status(409).json({ error: 'dataset_not_draft' });
    const itemHashes = db.prepare(`
      SELECT item_hash FROM dataset_items WHERE dataset_id = ? ORDER BY teaching_id
    `).all(dataset.id).map((item) => item.item_hash);
    const contentHash = hash(canonicalJson({
      schema: 'ii.dataset.v1',
      source_manifest_id: dataset.manifest_id,
      items: itemHashes,
    }));
    const now = Date.now();
    db.prepare(`
      UPDATE dataset_versions SET status = 'sealed', content_hash = ?, sealed_at = ? WHERE id = ?
    `).run(contentHash, now, dataset.id);
    audit('dataset.seal', 'dataset', dataset.id, {
      actor: req.body?.approved_by || req.get('x-reviewer-id'),
      content_hash: contentHash,
    });
    return res.json({ ok: true, id: dataset.id, status: 'sealed', content_hash: contentHash });
  });

  app.get('/admin/datasets', requireAdmin, (_req, res) => {
    const datasets = db.prepare(`
      SELECT id, manifest_id, status, content_hash, item_count, created_by, created_at, sealed_at
      FROM dataset_versions ORDER BY created_at DESC
    `).all();
    return res.json({ ok: true, datasets });
  });

  app.post('/admin/training-runs', requireAdmin, (req, res) => {
    const dataset = db.prepare(`
      SELECT * FROM dataset_versions WHERE id = ? AND status = 'sealed'
    `).get(req.body?.dataset_id);
    const config = safeJson(req.body?.config, 16_000);
    const environment = safeJson(req.body?.environment || {}, 16_000);
    const orchestrator = cleanText(req.body?.orchestrator, 80, 'external_gpu_runner');
    if (!dataset || !config || !environment) {
      return res.status(400).json({
        error: 'invalid_training_request',
        message: 'A sealed dataset and bounded config/environment metadata are required.',
      });
    }
    const encodedMetadata = JSON.stringify({ config, environment });
    const findings = scanSensitiveText(encodedMetadata);
    if (findings.length) return res.status(422).json({ error: 'secret_in_training_metadata', findings });
    const id = `train_${crypto.randomUUID()}`;
    const now = Date.now();
    db.prepare(`
      INSERT INTO training_runs (
        id, dataset_id, base_manifest_id, status, orchestrator,
        config_json, environment_json, created_at, updated_at
      ) VALUES (?, ?, ?, 'requested', ?, ?, ?, ?, ?)
    `).run(
      id,
      dataset.id,
      dataset.manifest_id,
      orchestrator,
      JSON.stringify(config),
      JSON.stringify(environment),
      now,
      now,
    );
    audit('training.request', 'training_run', id, { actor: req.body?.requested_by, orchestrator });
    return res.status(202).json({
      ok: true,
      id,
      status: 'requested',
      execution: 'external_orchestrator_required',
      message: 'No GPU training is performed by the coordinator.',
    });
  });

  app.post('/admin/training-runs/:id/report', requireAdmin, (req, res) => {
    const run = db.prepare('SELECT * FROM training_runs WHERE id = ?').get(req.params.id);
    if (!run) return res.status(404).json({ error: 'training_run_not_found' });
    const status = req.body?.status;
    if (!['running', 'evaluating', 'awaiting_approval', 'failed', 'cancelled'].includes(status)) {
      return res.status(400).json({ error: 'invalid_training_status' });
    }
    const metrics = safeJson(req.body?.metrics || {}, 32_000);
    const privacyReport = safeJson(req.body?.privacy_report || {}, 32_000);
    const artifactSha = req.body?.artifact_sha256;
    const artifactSize = Number(req.body?.artifact_size_bytes);
    let artifactUrl = null;
    try {
      if (req.body?.artifact_url) {
        const parsed = new URL(req.body.artifact_url);
        if (parsed.protocol !== 'https:') throw new Error('not https');
        artifactUrl = parsed.toString();
      }
    } catch {
      return res.status(400).json({ error: 'invalid_artifact_url' });
    }
    if (!metrics || !privacyReport) return res.status(400).json({ error: 'invalid_training_report' });
    if (status === 'awaiting_approval' && (
      metrics.benchmark_passed !== true
      || privacyReport.passed !== true
      || !/^[a-f0-9]{64}$/.test(String(artifactSha || ''))
      || !Number.isSafeInteger(artifactSize) || artifactSize <= 0
      || !artifactUrl
    )) {
      return res.status(422).json({
        error: 'release_gates_failed',
        message: 'Benchmark, privacy, artifact hash/size, and HTTPS artifact URL gates must all pass.',
      });
    }
    db.prepare(`
      UPDATE training_runs SET status = ?, metrics_json = ?, privacy_report_json = ?,
        artifact_sha256 = COALESCE(?, artifact_sha256),
        artifact_size_bytes = COALESCE(?, artifact_size_bytes),
        artifact_url = COALESCE(?, artifact_url), updated_at = ?
      WHERE id = ?
    `).run(
      status,
      JSON.stringify(metrics),
      JSON.stringify(privacyReport),
      artifactSha || null,
      Number.isSafeInteger(artifactSize) ? artifactSize : null,
      artifactUrl,
      Date.now(),
      run.id,
    );
    audit('training.report', 'training_run', run.id, { actor: req.body?.reported_by, status });
    return res.json({ ok: true, id: run.id, status });
  });

  app.post('/admin/training-runs/:id/approve', requireAdmin, (req, res) => {
    const approver = cleanText(req.body?.approved_by || req.get('x-reviewer-id'), 120);
    const run = db.prepare('SELECT * FROM training_runs WHERE id = ?').get(req.params.id);
    if (!run || run.status !== 'awaiting_approval' || !approver) {
      return res.status(409).json({ error: 'run_not_approvable' });
    }
    const metrics = JSON.parse(run.metrics_json || '{}');
    const privacy = JSON.parse(run.privacy_report_json || '{}');
    if (metrics.benchmark_passed !== true || privacy.passed !== true) {
      return res.status(422).json({ error: 'release_gates_failed' });
    }
    db.prepare(`
      UPDATE training_runs SET status = 'approved', approved_by = ?, updated_at = ? WHERE id = ?
    `).run(approver, Date.now(), run.id);
    audit('training.approve', 'training_run', run.id, { actor: approver });
    return res.json({ ok: true, id: run.id, status: 'approved', approved_by: approver });
  });

  app.post('/admin/training-runs/:id/publish', requireAdmin, (req, res) => {
    const run = db.prepare(`
      SELECT tr.*, dv.content_hash AS dataset_hash
      FROM training_runs tr JOIN dataset_versions dv ON dv.id = tr.dataset_id
      WHERE tr.id = ?
    `).get(req.params.id);
    if (!run || run.status !== 'approved') return res.status(409).json({ error: 'run_not_publishable' });
    const privateKeyPem = process.env.II_ADAPTER_SIGNING_PRIVATE_KEY;
    if (!privateKeyPem) {
      return res.status(503).json({
        error: 'adapter_signing_not_configured',
        message: 'Set II_ADAPTER_SIGNING_PRIVATE_KEY to an offline-approved Ed25519 key for publication.',
      });
    }
    let privateKey;
    try {
      privateKey = crypto.createPrivateKey(privateKeyPem.replace(/\\n/g, '\n'));
      if (privateKey.asymmetricKeyType !== 'ed25519') throw new Error('not Ed25519');
    } catch {
      return res.status(503).json({ error: 'invalid_adapter_signing_key' });
    }
    const id = `adapter_${crypto.randomUUID()}`;
    const manifest = {
      schema: 'ii.adapter-release.v1',
      id,
      training_run_id: run.id,
      dataset_id: run.dataset_id,
      dataset_sha256: run.dataset_hash,
      base_manifest_id: run.base_manifest_id,
      artifact: {
        sha256: run.artifact_sha256,
        size_bytes: run.artifact_size_bytes,
        url: run.artifact_url,
      },
      benchmark: JSON.parse(run.metrics_json),
      privacy_gate: JSON.parse(run.privacy_report_json),
      human_approval: { approved_by: run.approved_by },
      created_at: new Date().toISOString(),
    };
    const signature = crypto.sign(null, Buffer.from(canonicalJson(manifest)), privateKey).toString('base64');
    const publicKey = encodePublicKey(crypto.createPublicKey(privateKey));
    const now = Date.now();
    db.transaction(() => {
      db.prepare(`
        INSERT INTO adapter_releases (
          id, training_run_id, manifest_json, signature, signer_public_key,
          rollout_percent, status, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, 0, 'staged', ?, ?)
      `).run(id, run.id, canonicalJson(manifest), signature, publicKey, now, now);
      db.prepare(`UPDATE training_runs SET status = 'published', updated_at = ? WHERE id = ?`)
        .run(now, run.id);
      audit('adapter.publish', 'adapter_release', id, { actor: req.body?.published_by, training_run_id: run.id });
    })();
    return res.status(201).json({
      ok: true,
      id,
      status: 'staged',
      manifest,
      signature,
      signer_public_key: publicKey,
    });
  });

  app.post('/admin/adapters/:id/rollout', requireAdmin, (req, res) => {
    const percent = Number(req.body?.percent);
    if (!Number.isInteger(percent) || percent < 0 || percent > 100) {
      return res.status(400).json({ error: 'invalid_rollout_percent' });
    }
    const release = db.prepare('SELECT * FROM adapter_releases WHERE id = ?').get(req.params.id);
    if (!release || ['rolled_back', 'withdrawn'].includes(release.status)) {
      return res.status(409).json({ error: 'release_not_rollout_eligible' });
    }
    db.prepare(`
      UPDATE adapter_releases SET rollout_percent = ?, status = ?, updated_at = ? WHERE id = ?
    `).run(percent, percent > 0 ? 'active' : 'staged', Date.now(), release.id);
    audit('adapter.rollout', 'adapter_release', release.id, { actor: req.body?.approved_by, percent });
    return res.json({ ok: true, id: release.id, rollout_percent: percent });
  });

  app.post('/admin/adapters/:id/rollback', requireAdmin, (req, res) => {
    const release = db.prepare('SELECT * FROM adapter_releases WHERE id = ?').get(req.params.id);
    if (!release || !['active', 'staged'].includes(release.status)) {
      return res.status(409).json({ error: 'release_not_rollback_eligible' });
    }
    const targetId = cleanText(req.body?.target_release_id, 100, null);
    if (targetId) {
      const target = db.prepare('SELECT * FROM adapter_releases WHERE id = ?').get(targetId);
      if (!target || target.status === 'withdrawn') return res.status(400).json({ error: 'invalid_rollback_target' });
    }
    const now = Date.now();
    db.transaction(() => {
      db.prepare(`
        UPDATE adapter_releases SET status = 'rolled_back', rollout_percent = 0,
          rollback_release_id = ?, updated_at = ? WHERE id = ?
      `).run(targetId, now, release.id);
      if (targetId) {
        db.prepare(`
          UPDATE adapter_releases SET status = 'active', rollout_percent = 100, updated_at = ?
          WHERE id = ?
        `).run(now, targetId);
      }
      audit('adapter.rollback', 'adapter_release', release.id, { actor: req.body?.approved_by, target_release_id: targetId });
    })();
    return res.json({ ok: true, id: release.id, status: 'rolled_back', target_release_id: targetId });
  });

  app.get('/admin/training-runs', requireAdmin, (_req, res) => {
    const runs = db.prepare(`
      SELECT id, dataset_id, base_manifest_id, status, orchestrator, config_json,
             environment_json, metrics_json, privacy_report_json, artifact_sha256,
             artifact_size_bytes, artifact_url, approved_by, created_at, updated_at
      FROM training_runs ORDER BY created_at DESC
    `).all();
    return res.json({
      ok: true,
      execution_boundary: 'metadata_and_gates_only; external GPU runner required',
      runs: runs.map((row) => ({
        ...row,
        config: JSON.parse(row.config_json),
        environment: JSON.parse(row.environment_json),
        metrics: row.metrics_json ? JSON.parse(row.metrics_json) : null,
        privacy_report: row.privacy_report_json ? JSON.parse(row.privacy_report_json) : null,
        config_json: undefined,
        environment_json: undefined,
        metrics_json: undefined,
        privacy_report_json: undefined,
      })),
    });
  });
}

module.exports = { CONSENT_VERSION, installLearningRoutes, privacyScan, scanSensitiveText };
