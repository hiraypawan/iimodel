'use strict';

const { app, BrowserWindow, ipcMain, shell } = require('electron');
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const { downloadExpert, DEFAULT_MAX_EXPERT_BYTES } = require('./expert-store');
const { loadOrCreateHostIdentity, signRequest } = require('./host-identity');
const { LocalInferenceServer } = require('./inference-server');
const { artifactClaim, fetchSignedManifest, selectArtifact } = require('./model-config');
const { WorkerAgent } = require('./worker-agent');

const CLIENT_VERSION = '0.3.0';
const HEARTBEAT_INTERVAL_MS = 30_000;
const REQUEST_TIMEOUT_MS = 10_000;
const CONFIGURED_EXPERT_ID = process.env.II_EXPERT_ID === undefined
  ? null
  : Number.parseInt(process.env.II_EXPERT_ID, 10);
const INFERENCE_PORT = Number.parseInt(process.env.II_INFERENCE_PORT || '7777', 10);

let mainWindow;
let heartbeatTimer;
let heartbeatRunning = false;
let isQuitting = false;
let hostIdentity = null;
let expertArtifact = null;
let hostedArtifactClaim = null;
let inferenceService = null;
let workerAgent = null;
let activeManifest = null;
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

function publishState(patch) {
  hostState = { ...hostState, ...patch };
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send('host:status', hostState);
  }
  return hostState;
}

async function prepareExpertArtifact() {
  let sourceUrl;
  let expectedSha256;
  let expectedSize;
  let expertId = CONFIGURED_EXPERT_ID ?? 0;
  let artifactId = `legacy-expert-${expertId}`;

  if (process.env.II_MODEL_MANIFEST_ID) {
    activeManifest = await fetchSignedManifest({
      coordinatorUrl,
      manifestId: process.env.II_MODEL_MANIFEST_ID,
      trustedPublicKey: process.env.II_RELEASE_PUBLIC_KEY,
    });
    const selected = selectArtifact(activeManifest, {
      artifactId: process.env.II_ARTIFACT_ID,
      expertId: CONFIGURED_EXPERT_ID,
    });
    sourceUrl = selected.url;
    expectedSha256 = selected.sha256;
    expectedSize = selected.size_bytes;
    expertId = selected.expert_id ?? 0;
    artifactId = selected.id;
  } else {
    if (process.env.II_ALLOW_LEGACY_ARTIFACT !== 'true') {
      throw new Error('Set II_MODEL_MANIFEST_ID and II_RELEASE_PUBLIC_KEY; unsigned legacy artifacts are disabled.');
    }
    sourceUrl = process.env.II_EXPERT_SOURCE_URL;
    expectedSha256 = process.env.II_EXPERT_SHA256;
    if (!sourceUrl) throw new Error('II_EXPERT_SOURCE_URL is required in legacy artifact mode.');
  }

  if (!Number.isInteger(expertId) || expertId < 0 || expertId > 255) {
    throw new Error('II_EXPERT_ID must be an integer from 0 to 255.');
  }
  const configuredMaxBytes = Number.parseInt(
    process.env.II_EXPERT_MAX_BYTES || String(DEFAULT_MAX_EXPERT_BYTES),
    10,
  );
  if (expectedSize && expectedSize > configuredMaxBytes) {
    throw new Error(`Signed artifact is ${expectedSize} bytes, above II_EXPERT_MAX_BYTES.`);
  }

  const downloaded = await downloadExpert({
    homeDirectory: app.getPath('home'),
    expertId,
    sourceUrl,
    expectedSha256,
    maxBytes: configuredMaxBytes,
    onProgress: ({ bytesWritten, contentLength }) => {
      const total = contentLength ? ` / ${Math.round(contentLength / (1024 * 1024))} MB` : '';
      publishState({
        phase: 'preparing',
        message: `Downloading ${artifactId}: ${Math.round(bytesWritten / (1024 * 1024))} MB${total}`,
        expertBytesDownloaded: bytesWritten,
      });
    },
  });
  if (expectedSize && downloaded.sizeBytes !== expectedSize) {
    await fs.rm(downloaded.path, { force: true });
    throw new Error(`Artifact size mismatch: expected ${expectedSize}, received ${downloaded.sizeBytes}.`);
  }
  return { ...downloaded, artifactId, expertId };
}

async function loadInferenceRuntime(artifact) {
  if (!inferenceService?.server) throw new Error('Local inference service did not start.');
  const modelPath = activeManifest
    ? (artifact.format === 'gguf' ? artifact.path : null)
    : (process.env.II_INFERENCE_MODEL_PATH || (artifact.format === 'gguf' ? artifact.path : null));
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

async function postHostJson(
  route,
  action,
  payload,
  { timeoutMs = REQUEST_TIMEOUT_MS, includePublicKey = false } = {},
) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  const startedAt = performance.now();

  try {
    const response = await fetch(`${coordinatorUrl}${route}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(signRequest(hostIdentity, action, payload, { includePublicKey })),
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
    const { data, elapsedMs } = await postHostJson('/heartbeat', 'host:heartbeat', {
      artifacts: hostedArtifactClaim ? [hostedArtifactClaim] : [],
      endpoint: hostIdentity.inferenceEndpoint,
      status,
      latency_ms: hostState.latencyMs ?? null,
    }, { timeoutMs: status === 'offline' ? 2_000 : REQUEST_TIMEOUT_MS });

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
      message: 'Preparing signed host identity and model artifact…',
      error: null,
    });

    const identity = await loadOrCreateHostIdentity(stateDirectory());
    hostIdentity = identity;
    const artifact = await prepareExpertArtifact();
    expertArtifact = artifact;
    inferenceService.expertId = artifact.expertId;
    // Remove only the known PR #6 placeholder after a verified real artifact exists.
    await fs.rm(path.join(stateDirectory(), 'shard.bin'), { force: true });

    publishState({
      phase: 'preparing',
      message: `${artifact.artifactId} verified. Checking inference runtime…`,
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

    const manifestArtifact = activeManifest?.artifacts
      .find((item) => item.id === artifact.artifactId);
    hostedArtifactClaim = activeManifest && manifestArtifact
      ? artifactClaim(activeManifest, manifestArtifact, runtime.loaded)
      : null;

    const { data, elapsedMs } = await postHostJson('/register', 'host:register', {
      deviceName: os.hostname(),
      platform: `${process.platform}-${process.arch}`,
      clientVersion: CLIENT_VERSION,
      country: process.env.II_HOST_COUNTRY,
      endpoint: hostedArtifactClaim?.inference_ready ? runtime.endpoint : null,
      artifacts: hostedArtifactClaim ? [hostedArtifactClaim] : [],
      memoryGb: Math.round((os.totalmem() / (1024 ** 3)) * 10) / 10,
    }, { includePublicKey: true });

    hostIdentity = {
      ...identity,
      genesisNumber: data.genesisNumber,
      assignedExperts: data.experts,
      expertId: artifact.expertId,
      expertPath: artifact.path,
      expertSha256: artifact.sha256,
      expertSizeBytes: artifact.sizeBytes,
      inferenceEndpoint: hostedArtifactClaim?.inference_ready ? runtime.endpoint : null,
      inferenceLoaded: runtime.loaded,
      registeredAt: data.registeredAt,
      coordinatorUrl,
    };

    await sendHeartbeat('online');
    beginHeartbeatLoop();
    if (runtime.loaded && activeManifest?.role === 'worker') {
      workerAgent = new WorkerAgent({
        coordinatorUrl,
        identity: hostIdentity,
        inference: inferenceService,
        onState: (state) => publishState({ ...state, active: true }),
      });
      workerAgent.start();
    }
    const roleLabel = activeManifest?.role === 'worker'
      ? 'worker model'
      : Number.isInteger(manifestArtifact?.expert_id)
        ? `expert ${manifestArtifact.expert_id}`
        : 'artifact';
    return publishState({
      phase: 'hosting',
      message: runtime.loaded
        ? `${roleLabel} verified, loaded, and ready for real jobs`
        : `${roleLabel} verified and stored; executable inference is unavailable`,
      active: true,
      deviceId: data.deviceId,
      identityVerified: data.identityVerified,
      manifestId: activeManifest?.manifest_id || null,
      genesisNumber: data.genesisNumber,
      genesisMember: data.genesisMember,
      experts: Number.isInteger(manifestArtifact?.expert_id) ? [manifestArtifact.expert_id] : [],
      expertsLabel: Number.isInteger(manifestArtifact?.expert_id) ? String(manifestArtifact.expert_id) : 'Worker',
      expertPath: artifact.path,
      expertSizeBytes: artifact.sizeBytes,
      expertFormat: artifact.format,
      inferenceLoaded: runtime.loaded,
      inferenceError: runtime.error,
      localInferenceEndpoint: inferenceService.localEndpoint,
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
      preload: path.join(__dirname, 'preload.js'),
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
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
    expertId: CONFIGURED_EXPERT_ID ?? 0,
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
  workerAgent?.stop();
  if (hostState.active) void sendHeartbeat('offline');
  void inferenceService?.close();
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});
