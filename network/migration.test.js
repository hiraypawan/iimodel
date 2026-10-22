'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const fsp = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const Database = require('better-sqlite3');

test('migrates the PR #6 database without losing hosts or Genesis applications', async () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'ii-migration-test-'));
  const databasePath = path.join(directory, 'legacy.db');
  const legacy = new Database(databasePath);
  legacy.exec(`
    CREATE TABLE devices (
      id TEXT PRIMARY KEY,
      device_name TEXT NOT NULL,
      platform TEXT NOT NULL DEFAULT 'unknown',
      client_version TEXT NOT NULL DEFAULT '0.1.0',
      ip_address TEXT,
      endpoint TEXT,
      memory_gb REAL,
      shard_size_gb REAL NOT NULL DEFAULT 20,
      genesis_number INTEGER NOT NULL UNIQUE,
      status TEXT NOT NULL DEFAULT 'online',
      latency_ms REAL,
      registered_at INTEGER NOT NULL,
      last_seen INTEGER NOT NULL
    );
    CREATE TABLE device_experts (
      device_id TEXT NOT NULL,
      expert_id INTEGER NOT NULL,
      PRIMARY KEY (device_id, expert_id),
      FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE CASCADE
    );
    CREATE TABLE genesis_applications (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL,
      email TEXT NOT NULL COLLATE NOCASE UNIQUE,
      country TEXT NOT NULL,
      device TEXT,
      accepted_host_terms INTEGER NOT NULL DEFAULT 0,
      created_at INTEGER NOT NULL
    );
  `);
  legacy.prepare(`
    INSERT INTO devices (
      id, device_name, genesis_number, registered_at, last_seen
    ) VALUES (?, ?, ?, ?, ?)
  `).run('legacy-host', 'Legacy host', 1, 1_700_000_000_000, 1_700_000_000_000);
  for (let expertId = 12; expertId <= 24; expertId += 1) {
    legacy.prepare('INSERT INTO device_experts (device_id, expert_id) VALUES (?, ?)')
      .run('legacy-host', expertId);
  }
  legacy.prepare(`
    INSERT INTO genesis_applications (
      name, email, country, accepted_host_terms, created_at
    ) VALUES (?, ?, ?, 1, ?)
  `).run('Legacy Applicant', 'legacy@example.com', 'India', 1_700_000_000_000);
  legacy.close();

  process.env.II_DB_PATH = databasePath;
  const { db } = require('./server');
  const columns = db.prepare('PRAGMA table_info(devices)').all().map((column) => column.name);
  for (const required of ['last_heartbeat', 'country', 'experts_start', 'experts_end']) {
    assert.ok(columns.includes(required), `missing migrated column ${required}`);
  }
  assert.equal(db.prepare('SELECT COUNT(*) AS count FROM devices').get().count, 1);
  assert.deepEqual(
    db.prepare('SELECT email, country FROM genesis').get(),
    { email: 'legacy@example.com', country: 'India' },
  );
  db.close();
  await fsp.rm(directory, { recursive: true, force: true });
});
