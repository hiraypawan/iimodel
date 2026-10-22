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
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  completed_at INTEGER,
  FOREIGN KEY (manifest_id) REFERENCES model_manifests(manifest_id),
  FOREIGN KEY (lease_device_id) REFERENCES devices(id) ON DELETE SET NULL
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
