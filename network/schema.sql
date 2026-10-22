PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;

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
  registered_at INTEGER NOT NULL,
  last_seen INTEGER NOT NULL,
  last_heartbeat INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_devices_last_seen
  ON devices(last_seen DESC);

CREATE INDEX IF NOT EXISTS idx_devices_genesis_number
  ON devices(genesis_number);

CREATE TABLE IF NOT EXISTS device_experts (
  device_id TEXT NOT NULL,
  expert_id INTEGER NOT NULL CHECK (expert_id BETWEEN 0 AND 255),
  PRIMARY KEY (device_id, expert_id),
  FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_device_experts_expert
  ON device_experts(expert_id, device_id);

CREATE TABLE IF NOT EXISTS genesis (
  email TEXT PRIMARY KEY COLLATE NOCASE,
  country TEXT NOT NULL,
  created_at INTEGER NOT NULL
);
