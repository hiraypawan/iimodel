'use strict';

const { app, BrowserWindow, ipcMain, shell } = require('electron');
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const crypto = require('node:crypto');
const { downloadExpert, DEFAULT_MAX_EXPERT_BYTES } = require('./expert-store');
const { LocalInferenceServer } = require('./inference-server');

const CLIENT_VERSION = '0.2.0';
const HEARTBEAT_INTERVAL_MS = 30_000;
const REQUEST_TIMEOUT_MS = 10_000;
const EXPERT_ID = Number.parseInt(process.env.II_EXPERT_ID || '0', 10);
const INFERENCE_PORT = Number.parseInt(process.env.II_INFERENCE_PORT || '7777', 10);

let mainWindow;
let heartbeatTimer;
let heartbeatRunning = false;
let isQuitting = false;
let hostIdentity = null;
let expertArtifact = null;
let inferenceService = null;
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

async function prepareExpertArtifact() {
  const sourceUrl = process.env.II_EXPERT_SOURCE_URL;
  if (!sourceUrl) {
    throw new Error('II_EXPERT_SOURCE_URL is required before this device can host an expert.');
  }
  if (!Number.isInteger(EXPERT_ID) || EXPERT_ID < 0 || EXPERT_ID > 255) {
    throw new Error('II_EXPERT_ID must be an integer from 0 to 255.');
  }

  const configuredMaxBytes = Number.parseInt(
    process.env.II_EXPERT_MAX_BYTES || String(DEFAULT_MAX_EXPERT_BYTES),
    10,
  );
  return downloadExpert({
    homeDirectory: app.getPath('home'),
    expertId: EXPERT_ID,
    sourceUrl,
    expectedSha256: process.env.II_EXPERT_SHA256,
    maxBytes: configuredMaxBytes,
    onProgress: ({ bytesWritten, contentLength }) => {
      const total = contentLength ? ` / ${Math.round(contentLength / (1024 * 1024))} MB` : '';
      publishState({
        phase: 'preparing',
        message: `Downloading expert ${EXPERT_ID}: ${Math.round(bytesWritten / (1024 * 1024))} MB${total}`,
        expertBytesDownloaded: bytesWritten,
      });
    },
  });
}

async function loadInferenceRuntime(artifact) {
  if (!inferenceService?.server) throw new Error('Local inference service did not start.');
  const modelPath = process.env.II_INFERENCE_MODEL_PATH
    || (artifact.format === 'gguf' ? artifact.path : null);
  const loaded = await inferenceService.loadModel(modelPath);
  return {
    loaded,
    error: inferenceService.loadError,
    endpoint: loaded ? cleanPublicInferenceUrl(process.env.II_PUBLIC_INFERENCE_URL) : null,
  };
}

function cleanPublicInferenceUrl(value) {
  if (!value) return null;
  let parsed;
  try {
    parsed = new URL(value);
  } catch {
    throw new Error('II_PUBLIC_INFERENCE_URL must be a valid HTTPS URL.');
  }
  if (parsed.protocol !== 'https:'
    && !(parsed.protocol === 'http:' && ['127.0.0.1', 'localhost'].includes(parsed.hostname))) {
    throw new Error('II_PUBLIC_INFERENCE_URL must use HTTPS outside localhost.');
  }
  if (parsed.username || parsed.password) {
    throw new Error('II_PUBLIC_INFERENCE_URL must not contain credentials.');
  }
  parsed.hash = '';
  return parsed.toString();
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
      device_id: hostIdentity.deviceId,
      experts_start: hostIdentity.expertId,
      experts_end: hostIdentity.expertId,
      expert_size_bytes: hostIdentity.expertSizeBytes,
      endpoint: hostIdentity.inferenceEndpoint,
      status,
      latency_ms: hostState.latencyMs ?? null,
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
      message: `Preparing expert ${EXPERT_ID}…`,
      error: null,
    });

    const [identity, artifact] = await Promise.all([
      loadOrCreateIdentity(),
      prepareExpertArtifact(),
    ]);
    expertArtifact = artifact;
    // Remove only the known PR #6 placeholder after a verified real artifact exists.
    await fs.rm(path.join(stateDirectory(), 'shard.bin'), { force: true });

    publishState({
      phase: 'preparing',
      message: `Expert ${EXPERT_ID} verified. Checking inference runtime…`,
      expertPath: artifact.path,
      expertSizeBytes: artifact.sizeBytes,
      expertFormat: artifact.format,
      expertSha256: artifact.sha256,
    });
    const runtime = await loadInferenceRuntime(artifact);

    publishState({
      phase: 'connecting',
      message: 'Registering the expert with the coordinator…',
      inferenceLoaded: runtime.loaded,
      inferenceError: runtime.error,
    });

    const { data, elapsedMs } = await postJson('/register', {
      device_id: identity.deviceId,
      deviceName: os.hostname(),
      platform: `${process.platform}-${process.arch}`,
      clientVersion: CLIENT_VERSION,
      country: process.env.II_HOST_COUNTRY,
      endpoint: runtime.endpoint,
      expert_size_bytes: artifact.sizeBytes,
      memoryGb: Math.round((os.totalmem() / (1024 ** 3)) * 10) / 10,
    });

    const metadata = {
      ...identity,
      genesisNumber: data.genesisNumber,
      assignedExperts: data.experts,
      expertId: EXPERT_ID,
      expertPath: artifact.path,
      expertSha256: artifact.sha256,
      expertSizeBytes: artifact.sizeBytes,
      inferenceEndpoint: runtime.endpoint,
      inferenceLoaded: runtime.loaded,
      registeredAt: data.registeredAt,
      coordinatorUrl,
    };
    hostIdentity = metadata;
    await fs.writeFile(identityPath(), `${JSON.stringify(metadata, null, 2)}\n`, {
      encoding: 'utf8',
      mode: 0o600,
    });

    await sendHeartbeat('online');
    beginHeartbeatLoop();
    return publishState({
      phase: 'hosting',
      message: runtime.loaded
        ? `Expert ${EXPERT_ID} loaded and serving real inference`
        : `Expert ${EXPERT_ID} stored; inference is not loaded`,
      active: true,
      deviceId: data.deviceId,
      genesisNumber: data.genesisNumber,
      genesisMember: data.genesisMember,
      experts: [EXPERT_ID],
      expertsLabel: String(EXPERT_ID),
      expertPath: artifact.path,
      expertSizeBytes: artifact.sizeBytes,
      expertFormat: artifact.format,
      inferenceLoaded: runtime.loaded,
      inferenceError: runtime.error,
      localInferenceEndpoint: inferenceService.localEndpoint,
      latencyMs: elapsedMs,
      lastHeartbeat: new Date().toISOString(),
      nextHeartbeatInMs: data.heartbeatIntervalMs,
      error: runtime.loaded ? null : runtime.error,
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
ipcMain.handle('host:show-expert', async () => {
  if (!expertArtifact?.path) throw new Error('No expert artifact is stored yet.');
  await shell.showItemInFolder(expertArtifact.path);
  return expertArtifact.path;
});

app.whenReady().then(async () => {
  inferenceService = new LocalInferenceServer({
    host: '127.0.0.1',
    port: INFERENCE_PORT,
    expertId: EXPERT_ID,
  });
  try {
    await inferenceService.start();
  } catch (error) {
    publishState({
      phase: 'error',
      message: 'Local inference service could not start',
      error: error.message,
    });
  }

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
  void inferenceService?.close();
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});
