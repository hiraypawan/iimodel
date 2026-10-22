'use strict';

const { app, BrowserWindow, ipcMain, shell } = require('electron');
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const crypto = require('node:crypto');

const CLIENT_VERSION = '0.1.0';
const HEARTBEAT_INTERVAL_MS = 30_000;
const REQUEST_TIMEOUT_MS = 10_000;
const SHARD_SIZE_BYTES = 20 * 1024 * 1024 * 1024;

let mainWindow;
let heartbeatTimer;
let heartbeatRunning = false;
let isQuitting = false;
let hostIdentity = null;
let coordinatorUrl = normalizeCoordinatorUrl(
  process.env.II_COORDINATOR_URL || 'http://localhost:8787',
);

let hostState = {
  phase: 'idle',
  message: 'Ready to join the network',
  coordinatorUrl,
  active: false,
};

function normalizeCoordinatorUrl(value) {
  let parsed;
  try {
    parsed = new URL(value);
  } catch {
    throw new Error('II_COORDINATOR_URL must be a valid HTTP or HTTPS URL.');
  }

  if (!['http:', 'https:'].includes(parsed.protocol)) {
    throw new Error('II_COORDINATOR_URL must use HTTP or HTTPS.');
  }

  return parsed.toString().replace(/\/$/, '');
}

function stateDirectory() {
  return path.join(app.getPath('home'), '.ii');
}

function shardPath() {
  return path.join(stateDirectory(), 'shard.bin');
}

function identityPath() {
  return path.join(stateDirectory(), 'device.json');
}

function publishState(patch) {
  hostState = { ...hostState, ...patch };
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send('host:status', hostState);
  }
  return hostState;
}

async function loadOrCreateIdentity() {
  await fs.mkdir(stateDirectory(), { recursive: true, mode: 0o700 });

  try {
    const saved = JSON.parse(await fs.readFile(identityPath(), 'utf8'));
    if (typeof saved.deviceId === 'string' && /^[a-zA-Z0-9._:-]{3,128}$/.test(saved.deviceId)) {
      hostIdentity = saved;
      return saved;
    }
  } catch (error) {
    if (error.code !== 'ENOENT' && !(error instanceof SyntaxError)) throw error;
  }

  hostIdentity = {
    deviceId: `ii-${crypto.randomUUID()}`,
    createdAt: new Date().toISOString(),
  };
  await fs.writeFile(identityPath(), `${JSON.stringify(hostIdentity, null, 2)}\n`, {
    encoding: 'utf8',
    mode: 0o600,
  });
  return hostIdentity;
}

async function createSparseShard() {
  await fs.mkdir(stateDirectory(), { recursive: true, mode: 0o700 });
  const location = shardPath();
  const handle = await fs.open(location, 'a+', 0o600);

  try {
    const current = await handle.stat();
    if (current.size !== SHARD_SIZE_BYTES) {
      // Extending with truncate creates a sparse placeholder on supported file
      // systems; it does not download or write model weights in this MVP.
      await handle.truncate(SHARD_SIZE_BYTES);
      await handle.sync();
    }
  } finally {
    await handle.close();
  }

  const stats = await fs.stat(location);
  return { path: location, sizeBytes: stats.size };
}

async function postJson(route, payload, timeoutMs = REQUEST_TIMEOUT_MS) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  const startedAt = performance.now();

  try {
    const response = await fetch(`${coordinatorUrl}${route}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      signal: controller.signal,
    });
    const elapsedMs = Math.round(performance.now() - startedAt);
    const data = await response.json().catch(() => ({}));

    if (!response.ok) {
      throw new Error(data.message || `Coordinator returned HTTP ${response.status}.`);
    }
    return { data, elapsedMs };
  } catch (error) {
    if (error.name === 'AbortError') {
      throw new Error(`Coordinator did not respond within ${timeoutMs / 1000} seconds.`);
    }
    throw error;
  } finally {
    clearTimeout(timeout);
  }
}

async function sendHeartbeat(status = 'online') {
  if (!hostIdentity || heartbeatRunning) return;
  heartbeatRunning = true;

  try {
    const { data, elapsedMs } = await postJson('/heartbeat', {
      deviceId: hostIdentity.deviceId,
      status,
      latencyMs: hostState.latencyMs ?? null,
    }, status === 'offline' ? 2_000 : REQUEST_TIMEOUT_MS);

    if (status === 'online') {
      publishState({
        phase: 'hosting',
        message: 'Connected and hosting',
        active: true,
        latencyMs: elapsedMs,
        lastHeartbeat: data.serverTime,
        nextHeartbeatInMs: data.nextHeartbeatInMs,
      });
    }
  } catch (error) {
    if (status === 'online') {
      publishState({
        phase: 'reconnecting',
        message: 'Coordinator unavailable — retrying automatically',
        active: true,
        error: error.message,
      });
    }
  } finally {
    heartbeatRunning = false;
  }
}

function beginHeartbeatLoop() {
  clearInterval(heartbeatTimer);
  heartbeatTimer = setInterval(() => {
    void sendHeartbeat('online');
  }, HEARTBEAT_INTERVAL_MS);
  heartbeatTimer.unref?.();
}

async function startHosting() {
  if (hostState.active) return hostState;

  try {
    publishState({
      phase: 'preparing',
      message: 'Preparing your 20 GB sparse placeholder…',
      error: null,
    });

    const [identity, shard] = await Promise.all([
      loadOrCreateIdentity(),
      createSparseShard(),
    ]);

    publishState({
      phase: 'connecting',
      message: 'Registering with the coordinator…',
      shardPath: shard.path,
      shardSizeBytes: shard.sizeBytes,
    });

    const { data, elapsedMs } = await postJson('/register', {
      deviceId: identity.deviceId,
      deviceName: os.hostname(),
      platform: `${process.platform}-${process.arch}`,
      clientVersion: CLIENT_VERSION,
      memoryGb: Math.round((os.totalmem() / (1024 ** 3)) * 10) / 10,
    });

    const metadata = {
      ...identity,
      genesisNumber: data.genesisNumber,
      experts: data.experts,
      expertsLabel: data.expertsLabel,
      registeredAt: data.registeredAt,
      coordinatorUrl,
    };
    hostIdentity = metadata;
    await fs.writeFile(identityPath(), `${JSON.stringify(metadata, null, 2)}\n`, {
      encoding: 'utf8',
      mode: 0o600,
    });

    beginHeartbeatLoop();
    return publishState({
      phase: 'hosting',
      message: 'Connected and hosting',
      active: true,
      deviceId: data.deviceId,
      genesisNumber: data.genesisNumber,
      genesisMember: data.genesisMember,
      experts: data.experts,
      expertsLabel: data.expertsLabel,
      shardSizeGb: data.shardSizeGb,
      latencyMs: elapsedMs,
      lastHeartbeat: new Date().toISOString(),
      nextHeartbeatInMs: data.heartbeatIntervalMs,
      error: null,
    });
  } catch (error) {
    return publishState({
      phase: 'error',
      message: 'Could not start hosting',
      active: false,
      error: error.message,
    });
  }
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 920,
    height: 680,
    minWidth: 760,
    minHeight: 580,
    backgroundColor: '#090b0f',
    title: 'II Host',
    show: false,
    autoHideMenuBar: true,
    webPreferences: {
      // The renderer is a bundled local file with a strict CSP and external
      // navigation disabled. A preload bridge can replace this for production.
      nodeIntegration: true,
      contextIsolation: false,
      sandbox: false,
      devTools: process.env.NODE_ENV !== 'production',
    },
  });

  mainWindow.removeMenu();
  mainWindow.loadFile(path.join(__dirname, 'index.html'));
  mainWindow.once('ready-to-show', () => mainWindow.show());

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https:\/\//i.test(url)) void shell.openExternal(url);
    return { action: 'deny' };
  });

  mainWindow.webContents.on('will-navigate', (event, url) => {
    if (!url.startsWith('file://')) event.preventDefault();
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

ipcMain.handle('host:get-state', () => hostState);
ipcMain.handle('host:start', () => startHosting());
ipcMain.handle('host:show-shard', async () => {
  const location = shardPath();
  await shell.showItemInFolder(location);
  return location;
});

app.whenReady().then(() => {
  createWindow();
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('before-quit', () => {
  if (isQuitting) return;
  isQuitting = true;
  clearInterval(heartbeatTimer);
  if (hostState.active) void sendHeartbeat('offline');
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});
