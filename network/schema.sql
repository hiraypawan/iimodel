PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;

CREATE TABLE IF NOT EXISTS network_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS devices (
  id TEXT PRIMARY KEY,
  device_name TEXT NOT NULL,
  platform TEXT NOT NULL DEFAULT 'unknown',
  client_version TEXT NOT NULL DEFAULT '0.1.0',
  ip_address TEXT,
  endpoint TEXT,
  memory_gb REAL,
  shard_size_gb REAL NOT NULL DEFAULT 20,
  genesis_number INTEGER NOT NULL UNIQUE,
  status TEXT NOT NULL DEFAULT 'online' CHECK (status IN ('online', 'offline')),
  latency_ms REAL,
  country TEXT,
  experts_start INTEGER CHECK (experts_start BETWEEN 0 AND 255),
  experts_end INTEGER CHECK (experts_end BETWEEN 0 AND 255),
  public_key TEXT,
  last_signed_at INTEGER NOT NULL DEFAULT 0,
  registered_at INTEGER NOT NULL,
  last_seen INTEGER NOT NULL,
  last_heartbeat INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_devices_last_seen
  ON devices(last_seen DESC);

CREATE INDEX IF NOT EXISTS idx_devices_genesis_number
  ON devices(genesis_number);

CREATE TABLE IF NOT EXISTS host_revocations (
  device_id TEXT PRIMARY KEY,
  public_key TEXT,
  reason TEXT NOT NULL,
  revoked_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS device_experts (
  device_id TEXT NOT NULL,
  expert_id INTEGER NOT NULL CHECK (expert_id BETWEEN 0 AND 255),
  PRIMARY KEY (device_id, expert_id),
  FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_device_experts_expert
  ON device_experts(expert_id, device_id);

-- Desired placement is deliberately separate from artifacts a host has actually
-- verified. Assignments guide downloads; they never count as live capacity.
CREATE TABLE IF NOT EXISTS device_assignments (
  device_id TEXT NOT NULL,
  expert_id INTEGER NOT NULL CHECK (expert_id BETWEEN 0 AND 255),
  PRIMARY KEY (device_id, expert_id),
  FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_device_assignments_expert
  ON device_assignments(expert_id, device_id);

CREATE TABLE IF NOT EXISTS model_manifests (
  manifest_id TEXT PRIMARY KEY CHECK (length(manifest_id) = 64),
  model_id TEXT NOT NULL,
  version TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('worker', 'expert', 'phone')),
  runtime TEXT NOT NULL,
  manifest_json TEXT NOT NULL,
  signature TEXT NOT NULL,
  publisher_public_key TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
  created_at INTEGER NOT NULL,
  UNIQUE(model_id, version)
);

CREATE INDEX IF NOT EXISTS idx_model_manifests_active
  ON model_manifests(active, role, created_at DESC);

CREATE TABLE IF NOT EXISTS host_artifacts (
  device_id TEXT NOT NULL,
  manifest_id TEXT NOT NULL,
  artifact_id TEXT NOT NULL,
  sha256 TEXT NOT NULL CHECK (length(sha256) = 64),
  size_bytes INTEGER NOT NULL CHECK (size_bytes > 0),
  format TEXT NOT NULL,
  expert_id INTEGER CHECK (expert_id BETWEEN 0 AND 255),
  inference_ready INTEGER NOT NULL DEFAULT 0 CHECK (inference_ready IN (0, 1)),
  updated_at INTEGER NOT NULL,
  PRIMARY KEY (device_id, manifest_id, artifact_id),
  FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE CASCADE,
  FOREIGN KEY (manifest_id) REFERENCES model_manifests(manifest_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_host_artifacts_route
  ON host_artifacts(manifest_id, expert_id, inference_ready, updated_at DESC);

CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  manifest_id TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('queued', 'leased', 'completed', 'failed', 'cancelled')),
  prompt_encrypted TEXT NOT NULL,
  result_encrypted TEXT,
  access_token_hash TEXT NOT NULL,
  max_tokens INTEGER NOT NULL CHECK (max_tokens BETWEEN 1 AND 2048),
  max_attempts INTEGER NOT NULL DEFAULT 3 CHECK (max_attempts BETWEEN 1 AND 10),
  attempts INTEGER NOT NULL DEFAULT 0,
  lease_device_id TEXT,
  lease_token_hash TEXT,
  lease_expires_at INTEGER,
  error TEXT,
  metrics_json TEXT,
  job_kind TEXT NOT NULL DEFAULT 'inference' CHECK (job_kind IN ('inference', 'challenge')),
  expected_output_hash TEXT,
  required_results INTEGER NOT NULL DEFAULT 1 CHECK (required_results IN (1, 2)),
  desired_region TEXT,
  hedge_after_ms INTEGER NOT NULL DEFAULT 2000 CHECK (hedge_after_ms BETWEEN 250 AND 60000),
  max_parallel_attempts INTEGER NOT NULL DEFAULT 2 CHECK (max_parallel_attempts BETWEEN 1 AND 2),
  winning_attempt_id TEXT,
  comparison_status TEXT NOT NULL DEFAULT 'not_requested' CHECK (comparison_status IN ('not_requested', 'pending', 'matched', 'conflict')),
  priority INTEGER NOT NULL DEFAULT 0 CHECK (priority BETWEEN -100 AND 100),
  user_id TEXT,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  completed_at INTEGER,
  FOREIGN KEY (manifest_id) REFERENCES model_manifests(manifest_id),
  FOREIGN KEY (lease_device_id) REFERENCES devices(id) ON DELETE SET NULL,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_jobs_queue
  ON jobs(status, manifest_id, created_at ASC);

CREATE TABLE IF NOT EXISTS teachings (
  id TEXT PRIMARY KEY,
  manifest_id TEXT NOT NULL,
  prompt_encrypted TEXT NOT NULL,
  correction_encrypted TEXT NOT NULL,
  access_token_hash TEXT NOT NULL,
  language TEXT NOT NULL,
  category TEXT NOT NULL,
  consent_version TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'quarantined' CHECK (status IN ('quarantined', 'approved', 'rejected', 'deleted')),
  pii_scan_json TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  FOREIGN KEY (manifest_id) REFERENCES model_manifests(manifest_id)
);

CREATE INDEX IF NOT EXISTS idx_teachings_status
  ON teachings(status, created_at ASC);

CREATE TABLE IF NOT EXISTS genesis_hosts (
  device_id TEXT PRIMARY KEY,
  genesis_number INTEGER NOT NULL UNIQUE,
  verified_at INTEGER NOT NULL,
  FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS genesis (
  email TEXT PRIMARY KEY COLLATE NOCASE,
  country TEXT NOT NULL,
  created_at INTEGER NOT NULL
);

-- Runtime scheduling facts are kept separate from identity and artifact claims.
-- Everything in this table is self-declared and is treated as a scheduling hint,
-- never as hardware attestation.
CREATE TABLE IF NOT EXISTS worker_capabilities (
  device_id TEXT PRIMARY KEY,
  region TEXT,
  architecture TEXT NOT NULL DEFAULT 'unknown',
  cpu_cores INTEGER NOT NULL DEFAULT 1 CHECK (cpu_cores BETWEEN 1 AND 1024),
  memory_mb INTEGER NOT NULL DEFAULT 0 CHECK (memory_mb BETWEEN 0 AND 16777216),
  gpu_backend TEXT NOT NULL DEFAULT 'none',
  gpu_name TEXT,
  vram_mb INTEGER NOT NULL DEFAULT 0 CHECK (vram_mb BETWEEN 0 AND 16777216),
  max_concurrency INTEGER NOT NULL DEFAULT 1 CHECK (max_concurrency BETWEEN 1 AND 8),
  queue_depth INTEGER NOT NULL DEFAULT 0 CHECK (queue_depth BETWEEN 0 AND 10000),
  supported_runtimes_json TEXT NOT NULL DEFAULT '[]',
  benchmark_json TEXT,
  updated_at INTEGER NOT NULL,
  FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS worker_manifest_scores (
  device_id TEXT NOT NULL,
  manifest_id TEXT NOT NULL,
  successes INTEGER NOT NULL DEFAULT 0,
  failures INTEGER NOT NULL DEFAULT 0,
  challenge_passes INTEGER NOT NULL DEFAULT 0,
  challenge_failures INTEGER NOT NULL DEFAULT 0,
  conflicts INTEGER NOT NULL DEFAULT 0,
  ewma_latency_ms REAL,
  score REAL NOT NULL DEFAULT 0.5 CHECK (score BETWEEN 0 AND 1),
  last_event_at INTEGER NOT NULL,
  quarantine_until INTEGER,
  quarantine_reason TEXT,
  PRIMARY KEY (device_id, manifest_id),
  FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE CASCADE,
  FOREIGN KEY (manifest_id) REFERENCES model_manifests(manifest_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS reputation_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  device_id TEXT NOT NULL,
  manifest_id TEXT,
  job_id TEXT,
  attempt_id TEXT,
  source TEXT NOT NULL DEFAULT 'scheduler',
  event_type TEXT NOT NULL,
  delta REAL NOT NULL,
  reason TEXT,
  created_at INTEGER NOT NULL,
  FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_reputation_events_device
  ON reputation_events(device_id, created_at DESC);

CREATE TABLE IF NOT EXISTS job_attempts (
  id TEXT PRIMARY KEY,
  job_id TEXT NOT NULL,
  device_id TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('active', 'completed', 'failed', 'cancelled', 'expired')),
  lease_token_hash TEXT NOT NULL,
  lease_expires_at INTEGER NOT NULL,
  is_hedge INTEGER NOT NULL DEFAULT 0 CHECK (is_hedge IN (0, 1)),
  output_encrypted TEXT,
  output_hash TEXT,
  metrics_json TEXT,
  error TEXT,
  leased_at INTEGER NOT NULL,
  completed_at INTEGER,
  FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE,
  FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_job_attempts_active
  ON job_attempts(job_id, status, lease_expires_at);

CREATE INDEX IF NOT EXISTS idx_job_attempts_worker
  ON job_attempts(device_id, status, leased_at);

CREATE TABLE IF NOT EXISTS replica_targets (
  manifest_id TEXT NOT NULL,
  artifact_id TEXT NOT NULL,
  target_replicas INTEGER NOT NULL DEFAULT 2 CHECK (target_replicas BETWEEN 1 AND 1000),
  min_regions INTEGER NOT NULL DEFAULT 1 CHECK (min_regions BETWEEN 1 AND 100),
  updated_at INTEGER NOT NULL,
  PRIMARY KEY (manifest_id, artifact_id),
  FOREIGN KEY (manifest_id) REFERENCES model_manifests(manifest_id) ON DELETE CASCADE
);

-- Product identity and billing records contain provider identifiers, but never
-- card data or recoverable login/API tokens.
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  email TEXT NOT NULL UNIQUE COLLATE NOCASE,
  display_name TEXT,
  country TEXT,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suspended', 'deleted')),
  email_verified_at INTEGER,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS user_tokens (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  token_hash TEXT NOT NULL UNIQUE,
  label TEXT NOT NULL,
  scopes_json TEXT NOT NULL DEFAULT '[]',
  expires_at INTEGER,
  last_used_at INTEGER,
  revoked_at INTEGER,
  created_at INTEGER NOT NULL,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS identity_challenges (
  id TEXT PRIMARY KEY,
  email TEXT NOT NULL COLLATE NOCASE,
  token_hash TEXT NOT NULL UNIQUE,
  expires_at INTEGER NOT NULL,
  consumed_at INTEGER,
  attempts INTEGER NOT NULL DEFAULT 0,
  requested_ip_hash TEXT,
  created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_identity_challenges_email
  ON identity_challenges(email, created_at DESC);

CREATE TABLE IF NOT EXISTS provider_customers (
  user_id TEXT NOT NULL,
  provider TEXT NOT NULL,
  provider_customer_id TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  PRIMARY KEY (provider, provider_customer_id),
  UNIQUE(user_id, provider),
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS plans (
  code TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  billing_interval TEXT NOT NULL CHECK (billing_interval IN ('month', 'year', 'none')),
  currency TEXT NOT NULL DEFAULT 'INR',
  amount_minor INTEGER NOT NULL DEFAULT 0 CHECK (amount_minor >= 0),
  dodo_product_id TEXT,
  inference_jobs_quota INTEGER NOT NULL DEFAULT 0,
  max_tokens_quota INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS subscriptions (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  plan_code TEXT NOT NULL,
  provider TEXT NOT NULL DEFAULT 'dodo',
  provider_customer_id TEXT,
  provider_subscription_id TEXT UNIQUE,
  status TEXT NOT NULL CHECK (status IN (
    'pending', 'active', 'renewed', 'past_due', 'on_hold', 'paused',
    'cancelled', 'expired', 'refunded', 'chargeback'
  )),
  current_period_start INTEGER,
  current_period_end INTEGER,
  cancel_at_period_end INTEGER NOT NULL DEFAULT 0 CHECK (cancel_at_period_end IN (0, 1)),
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY (plan_code) REFERENCES plans(code)
);

CREATE INDEX IF NOT EXISTS idx_subscriptions_user
  ON subscriptions(user_id, status, updated_at DESC);

CREATE TABLE IF NOT EXISTS entitlements (
  user_id TEXT NOT NULL,
  feature TEXT NOT NULL,
  source_subscription_id TEXT,
  allowance INTEGER NOT NULL DEFAULT 0,
  used INTEGER NOT NULL DEFAULT 0,
  period_start INTEGER NOT NULL,
  period_end INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suspended', 'expired')),
  updated_at INTEGER NOT NULL,
  PRIMARY KEY (user_id, feature, period_start),
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY (source_subscription_id) REFERENCES subscriptions(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS checkout_sessions (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  plan_code TEXT NOT NULL,
  provider_checkout_id TEXT UNIQUE,
  checkout_url TEXT,
  status TEXT NOT NULL DEFAULT 'created' CHECK (status IN ('created', 'completed', 'expired', 'failed')),
  correlation_token_hash TEXT NOT NULL UNIQUE,
  provider_payload_hash TEXT,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY (plan_code) REFERENCES plans(code)
);

CREATE TABLE IF NOT EXISTS provider_payments (
  provider TEXT NOT NULL,
  provider_payment_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  subscription_id TEXT,
  checkout_id TEXT,
  status TEXT NOT NULL,
  amount_minor INTEGER,
  currency TEXT,
  updated_at INTEGER NOT NULL,
  PRIMARY KEY (provider, provider_payment_id),
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY (subscription_id) REFERENCES subscriptions(id) ON DELETE SET NULL,
  FOREIGN KEY (checkout_id) REFERENCES checkout_sessions(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS payment_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  provider TEXT NOT NULL,
  provider_event_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  payload_hash TEXT NOT NULL,
  payload_encrypted TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('received', 'processed', 'ignored', 'failed')),
  error TEXT,
  received_at INTEGER NOT NULL,
  processed_at INTEGER,
  UNIQUE(provider, provider_event_id)
);

CREATE TABLE IF NOT EXISTS usage_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id TEXT NOT NULL,
  feature TEXT NOT NULL,
  quantity INTEGER NOT NULL CHECK (quantity > 0),
  idempotency_key TEXT NOT NULL UNIQUE,
  job_id TEXT,
  created_at INTEGER NOT NULL,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS audit_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  actor_type TEXT NOT NULL,
  actor_id TEXT,
  action TEXT NOT NULL,
  target_type TEXT,
  target_id TEXT,
  details_json TEXT NOT NULL DEFAULT '{}',
  ip_hash TEXT,
  created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS fraud_flags (
  id TEXT PRIMARY KEY,
  user_id TEXT,
  provider_event_id TEXT,
  kind TEXT NOT NULL,
  severity TEXT NOT NULL CHECK (severity IN ('low', 'medium', 'high', 'critical')),
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'reviewing', 'resolved', 'dismissed')),
  details_json TEXT NOT NULL DEFAULT '{}',
  created_at INTEGER NOT NULL,
  resolved_at INTEGER,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS genesis_qualifications (
  user_id TEXT PRIMARY KEY,
  device_id TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL CHECK (status IN ('pending', 'qualified', 'revoked')),
  qualified_at INTEGER,
  reason TEXT,
  updated_at INTEGER NOT NULL,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE CASCADE
);

-- Human review, immutable dataset snapshots, and signed adapter rollout state.
CREATE TABLE IF NOT EXISTS teaching_reviews (
  id TEXT PRIMARY KEY,
  teaching_id TEXT NOT NULL,
  reviewer_id TEXT NOT NULL,
  decision TEXT NOT NULL CHECK (decision IN ('approved', 'rejected', 'needs_changes')),
  notes_encrypted TEXT,
  scan_json TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  FOREIGN KEY (teaching_id) REFERENCES teachings(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS dataset_versions (
  id TEXT PRIMARY KEY,
  manifest_id TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('draft', 'sealed', 'withdrawn')),
  content_hash TEXT,
  item_count INTEGER NOT NULL DEFAULT 0,
  provenance_json TEXT NOT NULL,
  created_by TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  sealed_at INTEGER,
  FOREIGN KEY (manifest_id) REFERENCES model_manifests(manifest_id)
);

CREATE TABLE IF NOT EXISTS dataset_items (
  dataset_id TEXT NOT NULL,
  teaching_id TEXT NOT NULL,
  item_hash TEXT NOT NULL,
  PRIMARY KEY (dataset_id, teaching_id),
  FOREIGN KEY (dataset_id) REFERENCES dataset_versions(id) ON DELETE CASCADE,
  FOREIGN KEY (teaching_id) REFERENCES teachings(id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS training_runs (
  id TEXT PRIMARY KEY,
  dataset_id TEXT NOT NULL,
  base_manifest_id TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('requested', 'running', 'evaluating', 'awaiting_approval', 'approved', 'rejected', 'published', 'failed', 'cancelled')),
  orchestrator TEXT NOT NULL,
  config_json TEXT NOT NULL,
  environment_json TEXT NOT NULL,
  metrics_json TEXT,
  privacy_report_json TEXT,
  artifact_sha256 TEXT,
  artifact_size_bytes INTEGER,
  artifact_url TEXT,
  approved_by TEXT,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  FOREIGN KEY (dataset_id) REFERENCES dataset_versions(id),
  FOREIGN KEY (base_manifest_id) REFERENCES model_manifests(manifest_id)
);

CREATE TABLE IF NOT EXISTS adapter_releases (
  id TEXT PRIMARY KEY,
  training_run_id TEXT NOT NULL UNIQUE,
  manifest_json TEXT NOT NULL,
  signature TEXT NOT NULL,
  signer_public_key TEXT NOT NULL,
  rollout_percent INTEGER NOT NULL DEFAULT 0 CHECK (rollout_percent BETWEEN 0 AND 100),
  status TEXT NOT NULL CHECK (status IN ('staged', 'active', 'rolled_back', 'withdrawn')),
  rollback_release_id TEXT,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  FOREIGN KEY (training_run_id) REFERENCES training_runs(id)
);

CREATE TABLE IF NOT EXISTS withdrawal_requests (
  id TEXT PRIMARY KEY,
  teaching_id TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('requested', 'blocked_by_sealed_dataset', 'completed')),
  affected_json TEXT NOT NULL,
  requested_at INTEGER NOT NULL,
  completed_at INTEGER,
  FOREIGN KEY (teaching_id) REFERENCES teachings(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS push_subscriptions (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  endpoint_hash TEXT NOT NULL UNIQUE,
  endpoint_encrypted TEXT NOT NULL,
  keys_encrypted TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  last_success_at INTEGER,
  disabled_at INTEGER,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
