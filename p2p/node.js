'use strict';

const fs = require('node:fs');
const fsp = require('node:fs/promises');
const http = require('node:http');
const path = require('node:path');
const crypto = require('node:crypto');
const { ContentStore, HASH_PATTERN, hashFile } = require('./content-store');
const {
  ReplayWindow,
  signDirectory,
  signPeerRequest,
  verifyDirectory,
  verifyPeerRequest,
} = require('./auth');

const DIRECTORY_TTL_MS = 2 * 60 * 1_000;
const MAX_CONTROL_BODY = 64 * 1024;

function json(res, status, value, extraHeaders = {}) {
  const body = Buffer.from(JSON.stringify(value));
  res.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Content-Length': body.length,
    'Cache-Control': 'no-store',
    'X-Content-Type-Options': 'nosniff',
    ...extraHeaders,
  });
  res.end(body);
}

async function readBody(req, maximum = MAX_CONTROL_BODY) {
  const chunks = [];
  let size = 0;
  for await (const chunk of req) {
    size += chunk.length;
    if (size > maximum) throw Object.assign(new Error('Request body exceeds limit.'), { statusCode: 413 });
    chunks.push(chunk);
  }
  return Buffer.concat(chunks);
}

function validPeerEndpoint(value) {
  try {
    const parsed = new URL(value);
    const localHttp = parsed.protocol === 'http:'
      && ['127.0.0.1', 'localhost', '[::1]'].includes(parsed.hostname);
    if (parsed.protocol !== 'https:' && !localHttp) return null;
    if (parsed.username || parsed.password || parsed.search || parsed.hash) return null;
    return parsed.toString().replace(/\/$/, '');
  } catch {
    return null;
  }
}

function parseRange(value, size, maximum) {
  const match = /^bytes=(\d+)-(\d+)$/.exec(String(value || ''));
  if (!match) return null;
  const start = Number(match[1]);
  const end = Number(match[2]);
  if (!Number.isSafeInteger(start) || !Number.isSafeInteger(end)
      || start < 0 || end < start || end >= size || end - start + 1 > maximum) return null;
  return { start, end };
}

class PeerNode {
  constructor({
    identity,
    stateDirectory,
    host = '127.0.0.1',
    port = 0,
    endpoint = null,
    bootstraps = [],
    relayUrl = null,
    maxArtifactBytes,
    maxChunkBytes,
    maxConcurrentTransfers = 4,
    maxBytesPerPeerMinute = 64 * 1024 * 1024,
    reputationFile = null,
    interruptRequestNumber = null,
    interruptOnceAfterBytes = null,
  }) {
    if (!identity?.privateKeyPem || !identity?.publicKey || !identity?.deviceId) {
      throw new Error('PeerNode requires a persisted Ed25519 identity.');
    }
    this.identity = identity;
    this.host = host;
    this.port = port;
    this.configuredEndpoint = endpoint;
    this.bootstraps = bootstraps;
    this.relayUrl = relayUrl ? validPeerEndpoint(relayUrl) : null;
    this.store = new ContentStore(path.join(stateDirectory, 'content'), { maxArtifactBytes, maxChunkBytes });
    this.maxConcurrentTransfers = maxConcurrentTransfers;
    this.maxBytesPerPeerMinute = maxBytesPerPeerMinute;
    this.reputationFile = reputationFile || path.join(stateDirectory, 'reputation.jsonl');
    this.replay = new ReplayWindow();
    this.peers = new Map();
    this.server = null;
    this.activeTransfers = 0;
    this.bandwidth = new Map();
    this.interruptRequestNumber = interruptRequestNumber;
    this.interruptOnceAfterBytes = interruptOnceAfterBytes;
    this.contentRequestCount = 0;
    this.interrupted = false;
  }

  get endpoint() {
    if (this.configuredEndpoint) return this.configuredEndpoint;
    const address = this.server?.address();
    if (!address || typeof address === 'string') return null;
    const host = ['0.0.0.0', '::'].includes(this.host) ? '127.0.0.1' : this.host;
    return `http://${host.includes(':') ? `[${host}]` : host}:${address.port}`;
  }

  async init() {
    await this.store.init();
    await fsp.mkdir(path.dirname(this.reputationFile), { recursive: true, mode: 0o700 });
    return this;
  }

  async start() {
    await this.init();
    if (this.server) return this.endpoint;
    this.server = http.createServer((req, res) => void this.handle(req, res));
    await new Promise((resolve, reject) => {
      this.server.once('error', reject);
      this.server.listen(this.port, this.host, resolve);
    });
    await this.registerSelf();
    await this.announce().catch((error) => this.reputation({
      peer_id: null, event: 'discovery_failure', reason: error.message,
    }));
    return this.endpoint;
  }

  async close() {
    if (!this.server) return;
    await new Promise((resolve) => this.server.close(resolve));
    this.server = null;
  }

  async reputation(event) {
    const record = { schema: 'ii.p2p-reputation.v1', at: new Date().toISOString(), ...event };
    await fsp.appendFile(this.reputationFile, `${JSON.stringify(record)}\n`, { mode: 0o600 }).catch(() => {});
  }

  async registerSelf() {
    const content = (await this.store.list()).map((item) => item.hash);
    this.peers.set(this.identity.deviceId, {
      peer_id: this.identity.deviceId,
      public_key: this.identity.publicKey,
      endpoint: this.endpoint,
      content,
      relay: true,
      updated_at: Date.now(),
    });
  }

  async announce() {
    await this.registerSelf();
    const body = Buffer.from(JSON.stringify({
      endpoint: this.endpoint,
      content: this.peers.get(this.identity.deviceId).content,
      relay: true,
    }));
    for (const bootstrap of this.bootstraps) {
      const base = validPeerEndpoint(bootstrap.url);
      if (!base) throw new Error('Invalid bootstrap URL.');
      const route = '/v1/peers/announce';
      const response = await fetch(`${base}${route}`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...signPeerRequest(this.identity, 'POST', route, { body }),
        },
        body,
        signal: AbortSignal.timeout(5_000),
      });
      if (!response.ok) throw new Error(`Bootstrap announcement returned HTTP ${response.status}.`);
    }
  }

  authenticate(req, body = Buffer.alloc(0)) {
    return verifyPeerRequest(req, body, this.replay);
  }

  consumeBandwidth(peerId, bytes) {
    const now = Date.now();
    const current = this.bandwidth.get(peerId);
    const bucket = !current || current.startedAt < now - 60_000
      ? { startedAt: now, bytes: 0 }
      : current;
    if (bucket.bytes + bytes > this.maxBytesPerPeerMinute) return false;
    bucket.bytes += bytes;
    this.bandwidth.set(peerId, bucket);
    return true;
  }

  async handle(req, res) {
    try {
      const url = new URL(req.url || '/', 'http://peer.local');
      if (req.method === 'GET' && url.pathname === '/health') {
        return json(res, 200, {
          ok: true,
          service: 'ii-local-p2p',
          peer_id: this.identity.deviceId,
          endpoint: this.endpoint,
          transport: 'authenticated-http-range-v1',
        });
      }

      if (req.method === 'POST' && url.pathname === '/v1/peers/announce') {
        const body = await readBody(req);
        const auth = this.authenticate(req, body);
        if (!auth.ok) return json(res, 401, { error: auth.error });
        let payload;
        try { payload = JSON.parse(body); } catch { return json(res, 400, { error: 'invalid_json' }); }
        const endpoint = validPeerEndpoint(payload.endpoint);
        const content = Array.isArray(payload.content)
          ? [...new Set(payload.content.filter((item) => HASH_PATTERN.test(item)))].slice(0, 2_048)
          : [];
        if (!endpoint) return json(res, 400, { error: 'invalid_peer_endpoint' });
        this.peers.set(auth.peerId, {
          peer_id: auth.peerId,
          public_key: auth.publicKey,
          endpoint,
          content,
          relay: payload.relay === true,
          updated_at: Date.now(),
        });
        await this.reputation({ peer_id: auth.peerId, event: 'peer_announcement_accepted' });
        return json(res, 202, { ok: true });
      }

      if (req.method === 'GET' && url.pathname === '/v1/peers') {
        const auth = this.authenticate(req);
        if (!auth.ok) return json(res, 401, { error: auth.error });
        const hash = url.searchParams.get('hash');
        if (!HASH_PATTERN.test(String(hash || ''))) return json(res, 400, { error: 'invalid_content_hash' });
        await this.registerSelf();
        const peers = [...this.peers.values()]
          .filter((peer) => peer.updated_at >= Date.now() - DIRECTORY_TTL_MS && peer.content.includes(hash))
          .map(({ peer_id, public_key, endpoint, relay, updated_at }) => ({
            peer_id, public_key, endpoint, relay, updated_at,
          }));
        return json(res, 200, signDirectory(this.identity, {
          hash,
          peers,
          issued_at: new Date().toISOString(),
          expires_at: new Date(Date.now() + 60_000).toISOString(),
        }));
      }

      const metaMatch = /^\/v1\/content\/([a-f0-9]{64})\/meta$/.exec(url.pathname);
      if (req.method === 'GET' && metaMatch) {
        const auth = this.authenticate(req);
        if (!auth.ok) return json(res, 401, { error: auth.error });
        const descriptor = await this.store.descriptor(metaMatch[1], { verify: true });
        if (!descriptor) return json(res, 404, { error: 'content_not_found' });
        return json(res, 200, descriptor);
      }

      const contentMatch = /^\/v1\/content\/([a-f0-9]{64})$/.exec(url.pathname);
      if (req.method === 'GET' && contentMatch) {
        const auth = this.authenticate(req);
        if (!auth.ok) return json(res, 401, { error: auth.error });
        if (this.activeTransfers >= this.maxConcurrentTransfers) {
          return json(res, 429, { error: 'transfer_concurrency_limit' }, { 'Retry-After': '1' });
        }
        const descriptor = await this.store.descriptor(contentMatch[1], { verify: true });
        if (!descriptor) return json(res, 404, { error: 'content_not_found_or_corrupt' });
        const range = parseRange(req.headers.range, descriptor.size, descriptor.max_chunk_bytes);
        if (!range) return json(res, 416, { error: 'bounded_byte_range_required' }, {
          'Content-Range': `bytes */${descriptor.size}`,
        });
        const length = range.end - range.start + 1;
        if (!this.consumeBandwidth(auth.peerId, length)) {
          return json(res, 429, { error: 'peer_bandwidth_limit' }, { 'Retry-After': '60' });
        }
        this.activeTransfers += 1;
        try {
          const result = await this.store.readRange(contentMatch[1], range.start, range.end);
          this.contentRequestCount += 1;
          res.writeHead(206, {
            'Content-Type': 'application/octet-stream',
            'Content-Length': result.bytes.length,
            'Content-Range': `bytes ${range.start}-${range.end}/${descriptor.size}`,
            'Accept-Ranges': 'bytes',
            'X-II-Content-SHA256': descriptor.hash,
            'Cache-Control': 'private, no-store',
            'X-Content-Type-Options': 'nosniff',
          });
          if (!this.interrupted
              && this.interruptRequestNumber === this.contentRequestCount
              && Number.isInteger(this.interruptOnceAfterBytes)
              && this.interruptOnceAfterBytes > 0
              && this.interruptOnceAfterBytes < result.bytes.length) {
            this.interrupted = true;
            res.write(result.bytes.subarray(0, this.interruptOnceAfterBytes));
            res.destroy();
            await this.reputation({ peer_id: auth.peerId, event: 'test_transport_interruption', hash: descriptor.hash });
            return undefined;
          }
          res.end(result.bytes);
          await this.reputation({ peer_id: auth.peerId, event: 'chunk_served', hash: descriptor.hash, bytes: length });
          return undefined;
        } finally {
          this.activeTransfers -= 1;
        }
      }

      if (req.method === 'POST' && url.pathname === '/v1/relay') {
        const body = await readBody(req);
        const auth = this.authenticate(req, body);
        if (!auth.ok) return json(res, 401, { error: auth.error });
        let payload;
        try { payload = JSON.parse(body); } catch { return json(res, 400, { error: 'invalid_json' }); }
        if (!HASH_PATTERN.test(String(payload.hash || ''))
            || !Number.isSafeInteger(payload.start) || !Number.isSafeInteger(payload.end)) {
          return json(res, 400, { error: 'invalid_relay_request' });
        }
        let peer = this.peers.get(payload.target_peer_id);
        if (!peer || !peer.content.includes(payload.hash)) {
          const discovered = await this.lookupPeers(payload.hash);
          peer = discovered.find((item) => item.peer_id === payload.target_peer_id);
        }
        if (!peer || peer.peer_id === this.identity.deviceId) return json(res, 404, { error: 'relay_target_not_found' });
        try {
          const chunk = await this.fetchChunk(peer, payload.hash, payload.start, payload.end);
          if (!this.consumeBandwidth(auth.peerId, chunk.length)) {
            return json(res, 429, { error: 'peer_bandwidth_limit' }, { 'Retry-After': '60' });
          }
          res.writeHead(206, {
            'Content-Type': 'application/octet-stream',
            'Content-Length': chunk.length,
            'Content-Range': `bytes ${payload.start}-${payload.end}/*`,
            'X-II-Relayed-From': peer.peer_id,
            'X-II-Content-SHA256': payload.hash,
            'Cache-Control': 'private, no-store',
          });
          res.end(chunk);
          await this.reputation({ peer_id: auth.peerId, upstream_peer_id: peer.peer_id, event: 'relayed_chunk', hash: payload.hash, bytes: chunk.length });
          return undefined;
        } catch (error) {
          await this.reputation({ peer_id: peer.peer_id, event: 'relay_upstream_failure', hash: payload.hash, reason: error.message });
          return json(res, 502, { error: 'relay_upstream_failure' });
        }
      }

      return json(res, 404, { error: 'not_found' });
    } catch (error) {
      return json(res, error.statusCode || 500, {
        error: error.statusCode ? 'invalid_request' : 'peer_internal_error',
        message: error.statusCode ? error.message : 'Peer request failed.',
      });
    }
  }

  async authenticatedFetch(base, route, options = {}) {
    const body = options.body || Buffer.alloc(0);
    const headers = signPeerRequest(this.identity, options.method || 'GET', route, {
      range: options.range || '', body,
    });
    return fetch(`${base}${route}`, {
      method: options.method || 'GET',
      headers: {
        ...headers,
        ...(options.contentType ? { 'Content-Type': options.contentType } : {}),
      },
      body: (options.method || 'GET') === 'GET' ? undefined : body,
      signal: AbortSignal.timeout(options.timeoutMs || 10_000),
    });
  }

  async lookupPeers(hash) {
    if (!HASH_PATTERN.test(hash)) throw new Error('Invalid content hash.');
    const found = new Map();
    await this.registerSelf();
    const local = this.peers.get(this.identity.deviceId);
    if (local.content.includes(hash)) found.set(local.peer_id, local);
    for (const bootstrap of this.bootstraps) {
      const base = validPeerEndpoint(bootstrap.url);
      if (!base || !bootstrap.publicKey) continue;
      const route = `/v1/peers?hash=${encodeURIComponent(hash)}`;
      try {
        const response = await this.authenticatedFetch(base, route);
        const envelope = await response.json();
        if (!response.ok || !verifyDirectory(envelope, bootstrap.publicKey)) {
          throw new Error('Bootstrap directory signature failed.');
        }
        if (envelope.document.hash !== hash || Date.parse(envelope.document.expires_at) < Date.now()) {
          throw new Error('Bootstrap directory response is stale or for another hash.');
        }
        for (const peer of envelope.document.peers || []) {
          const endpoint = validPeerEndpoint(peer.endpoint);
          if (endpoint && peer.peer_id && peer.public_key) {
            const record = { ...peer, endpoint, content: [hash], updated_at: Date.now() };
            this.peers.set(peer.peer_id, record);
            found.set(peer.peer_id, record);
          }
        }
      } catch (error) {
        await this.reputation({ peer_id: null, event: 'directory_lookup_failure', hash, reason: error.message });
      }
    }
    return [...found.values()];
  }

  async fetchDescriptor(peer, hash) {
    const route = `/v1/content/${hash}/meta`;
    const response = await this.authenticatedFetch(peer.endpoint, route);
    const descriptor = await response.json().catch(() => ({}));
    if (!response.ok || descriptor.hash !== hash || !Number.isSafeInteger(descriptor.size)) {
      throw new Error(`Peer metadata failed with HTTP ${response.status}.`);
    }
    return descriptor;
  }

  async fetchChunk(peer, hash, start, end) {
    const route = `/v1/content/${hash}`;
    const range = `bytes=${start}-${end}`;
    const response = await this.authenticatedFetch(peer.endpoint, route, { range, timeoutMs: 15_000 });
    if (response.status !== 206 || response.headers.get('x-ii-content-sha256') !== hash) {
      throw new Error(`Peer chunk failed with HTTP ${response.status}.`);
    }
    const bytes = Buffer.from(await response.arrayBuffer());
    if (bytes.length !== end - start + 1) throw new Error('Peer returned a truncated chunk.');
    return bytes;
  }

  async relayChunk(relayUrl, peer, hash, start, end) {
    const route = '/v1/relay';
    const body = Buffer.from(JSON.stringify({
      target_peer_id: peer.peer_id, hash, start, end,
    }));
    const response = await this.authenticatedFetch(relayUrl, route, {
      method: 'POST', body, contentType: 'application/json', timeoutMs: 20_000,
    });
    if (response.status !== 206 || response.headers.get('x-ii-content-sha256') !== hash) {
      throw new Error(`Relay chunk failed with HTTP ${response.status}.`);
    }
    const bytes = Buffer.from(await response.arrayBuffer());
    if (bytes.length !== end - start + 1) throw new Error('Relay returned a truncated chunk.');
    return bytes;
  }

  async fallbackChunk(fallbackUrl, start, end) {
    const endpoint = validPeerEndpoint(fallbackUrl);
    if (!endpoint) throw new Error('Fallback URL must use HTTPS (or loopback HTTP for development).');
    const response = await fetch(endpoint, {
      headers: { Range: `bytes=${start}-${end}` },
      signal: AbortSignal.timeout(20_000),
    });
    if (![200, 206].includes(response.status)) throw new Error(`HTTP fallback returned ${response.status}.`);
    const bytes = Buffer.from(await response.arrayBuffer());
    if (response.status === 200 && start !== 0) throw new Error('HTTP fallback does not support resume ranges.');
    if (bytes.length !== end - start + 1) throw new Error('HTTP fallback returned an unexpected byte count.');
    return bytes;
  }

  async download({ hash, size = null, fallbackUrl = null, forceRelay = false, retryCount = 4 }) {
    if (!HASH_PATTERN.test(hash)) throw new Error('Invalid content hash.');
    const existing = await this.store.descriptor(hash, { verify: true });
    if (existing) return { ...existing, path: this.store.artifactPath(hash), source: 'existing' };
    const peers = await this.lookupPeers(hash);
    let descriptor = null;
    let descriptorPeer = null;
    for (const peer of peers) {
      if (peer.peer_id === this.identity.deviceId) continue;
      try {
        descriptor = await this.fetchDescriptor(peer, hash);
        descriptorPeer = peer;
        break;
      } catch (error) {
        await this.reputation({ peer_id: peer.peer_id, event: 'metadata_failure', hash, reason: error.message });
      }
    }
    const expectedSize = descriptor?.size || size;
    if (!Number.isSafeInteger(expectedSize) || expectedSize <= 0 || expectedSize > this.store.maxArtifactBytes) {
      throw new Error('No bounded content size is available from peers or fallback metadata.');
    }
    if (size && expectedSize !== size) throw new Error('Peer content size disagrees with the signed artifact size.');
    const chunkSize = Math.min(descriptor?.max_chunk_bytes || this.store.maxChunkBytes, this.store.maxChunkBytes);
    const partial = this.store.partialPath(hash);
    await fsp.mkdir(path.dirname(partial), { recursive: true, mode: 0o700 });
    let offset = 0;
    try {
      const stat = await fsp.stat(partial);
      if (!stat.isFile() || stat.size > expectedSize) await this.store.rejectPartial(hash, 'invalid-size');
      else offset = stat.size;
    } catch (error) {
      if (error.code !== 'ENOENT') throw error;
    }
    const handle = await fsp.open(partial, offset ? 'a' : 'w', 0o600);
    let lastSource = null;
    try {
      while (offset < expectedSize) {
        const end = Math.min(expectedSize - 1, offset + chunkSize - 1);
        let chunk = null;
        let lastError = null;
        for (let attempt = 0; attempt < retryCount && !chunk; attempt += 1) {
          const peer = peers.filter((item) => item.peer_id !== this.identity.deviceId)[attempt % Math.max(1, peers.length)] || descriptorPeer;
          try {
            if (peer && forceRelay && this.relayUrl) {
              chunk = await this.relayChunk(this.relayUrl, peer, hash, offset, end);
              lastSource = `relay:${peer.peer_id}`;
            } else if (peer) {
              chunk = await this.fetchChunk(peer, hash, offset, end);
              lastSource = `peer:${peer.peer_id}`;
            } else if (fallbackUrl) {
              chunk = await this.fallbackChunk(fallbackUrl, offset, end);
              lastSource = 'http-fallback';
            } else {
              throw new Error('No peer or HTTP fallback is available.');
            }
          } catch (error) {
            lastError = error;
            if (peer) await this.reputation({ peer_id: peer.peer_id, event: 'chunk_failure', hash, offset, reason: error.message });
          }
        }
        if (!chunk && fallbackUrl) {
          try {
            chunk = await this.fallbackChunk(fallbackUrl, offset, end);
            lastSource = 'http-fallback';
          } catch (error) { lastError = error; }
        }
        if (!chunk) throw lastError || new Error('Chunk transfer failed.');
        await handle.write(chunk, 0, chunk.length, null);
        await handle.sync();
        offset += chunk.length;
        await this.reputation({
          peer_id: lastSource?.startsWith('peer:') || lastSource?.startsWith('relay:')
            ? lastSource.slice(lastSource.indexOf(':') + 1)
            : null,
          event: 'chunk_received', hash, bytes: chunk.length, offset, source: lastSource,
        });
      }
    } finally {
      await handle.close();
    }
    const actual = await hashFile(partial);
    if (actual !== hash) {
      await this.store.rejectPartial(hash, 'sha256-mismatch');
      await this.reputation({ peer_id: null, event: 'content_corruption_rejected', hash, actual_hash: actual, source: lastSource });
      throw new Error('Downloaded content failed SHA-256 verification and was quarantined.');
    }
    const destination = this.store.artifactPath(hash);
    await fsp.mkdir(path.dirname(destination), { recursive: true, mode: 0o700 });
    await fsp.rename(partial, destination);
    await this.registerSelf();
    await this.announce().catch(() => {});
    await this.reputation({ peer_id: null, event: 'content_verified', hash, bytes: expectedSize, source: lastSource });
    return { hash, size: expectedSize, path: destination, source: lastSource };
  }
}

module.exports = { PeerNode, parseRange, validPeerEndpoint };
