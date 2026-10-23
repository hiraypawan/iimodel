'use strict';

const fs = require('node:fs');
const fsp = require('node:fs/promises');
const path = require('node:path');
const crypto = require('node:crypto');

const HASH_PATTERN = /^[a-f0-9]{64}$/;

async function hashFile(filePath) {
  const digest = crypto.createHash('sha256');
  const stream = fs.createReadStream(filePath);
  for await (const chunk of stream) digest.update(chunk);
  return digest.digest('hex');
}

class ContentStore {
  constructor(root, {
    maxArtifactBytes = 32 * 1024 * 1024 * 1024,
    maxChunkBytes = 1024 * 1024,
  } = {}) {
    this.root = root;
    this.maxArtifactBytes = maxArtifactBytes;
    this.maxChunkBytes = maxChunkBytes;
  }

  async init() {
    await fsp.mkdir(path.join(this.root, 'sha256'), { recursive: true, mode: 0o700 });
    await fsp.mkdir(path.join(this.root, 'partial'), { recursive: true, mode: 0o700 });
    await fsp.mkdir(path.join(this.root, 'rejected'), { recursive: true, mode: 0o700 });
    return this;
  }

  artifactPath(hash) {
    if (!HASH_PATTERN.test(hash)) throw new Error('Content hash must be 64 lowercase hexadecimal characters.');
    return path.join(this.root, 'sha256', hash.slice(0, 2), hash);
  }

  partialPath(hash) {
    if (!HASH_PATTERN.test(hash)) throw new Error('Content hash must be 64 lowercase hexadecimal characters.');
    return path.join(this.root, 'partial', `${hash}.part`);
  }

  async importFile(source, expectedHash = null) {
    const stat = await fsp.stat(source);
    if (!stat.isFile() || stat.size <= 0 || stat.size > this.maxArtifactBytes) {
      throw new Error('Artifact is empty, not a file, or exceeds the configured size limit.');
    }
    const actualHash = await hashFile(source);
    if (expectedHash && actualHash !== expectedHash) throw new Error('Source artifact hash does not match its expected content address.');
    const destination = this.artifactPath(actualHash);
    await fsp.mkdir(path.dirname(destination), { recursive: true, mode: 0o700 });
    const temporary = `${destination}.${process.pid}.${crypto.randomUUID()}.tmp`;
    await fsp.copyFile(source, temporary, fs.constants.COPYFILE_EXCL);
    const copiedHash = await hashFile(temporary);
    if (copiedHash !== actualHash) {
      await fsp.rm(temporary, { force: true });
      throw new Error('Artifact changed or was corrupted during import.');
    }
    await fsp.rename(temporary, destination).catch(async (error) => {
      if (error.code !== 'EEXIST') throw error;
      await fsp.rm(temporary, { force: true });
    });
    return { hash: actualHash, size: stat.size, path: destination };
  }

  async descriptor(hash, { verify = true } = {}) {
    const artifact = this.artifactPath(hash);
    let stat;
    try { stat = await fsp.stat(artifact); } catch (error) {
      if (error.code === 'ENOENT') return null;
      throw error;
    }
    if (!stat.isFile() || stat.size <= 0 || stat.size > this.maxArtifactBytes) return null;
    if (verify && await hashFile(artifact) !== hash) return null;
    return {
      schema: 'ii.content.v1',
      hash,
      algorithm: 'sha256',
      size: stat.size,
      max_chunk_bytes: this.maxChunkBytes,
    };
  }

  async list() {
    const result = [];
    const base = path.join(this.root, 'sha256');
    let prefixes = [];
    try { prefixes = await fsp.readdir(base); } catch { return []; }
    for (const prefix of prefixes) {
      let names = [];
      try { names = await fsp.readdir(path.join(base, prefix)); } catch { continue; }
      for (const hash of names) {
        if (HASH_PATTERN.test(hash)) {
          const descriptor = await this.descriptor(hash, { verify: false });
          if (descriptor) result.push(descriptor);
        }
      }
    }
    return result;
  }

  async readRange(hash, start, end) {
    const descriptor = await this.descriptor(hash, { verify: true });
    if (!descriptor) {
      const error = new Error('Content is absent or failed its content-address verification.');
      error.code = 'CONTENT_UNAVAILABLE';
      throw error;
    }
    if (!Number.isSafeInteger(start) || !Number.isSafeInteger(end)
        || start < 0 || end < start || end >= descriptor.size
        || end - start + 1 > this.maxChunkBytes) {
      const error = new Error('Requested range is invalid or exceeds the chunk limit.');
      error.code = 'INVALID_RANGE';
      throw error;
    }
    const length = end - start + 1;
    const handle = await fsp.open(this.artifactPath(hash), 'r');
    try {
      const output = Buffer.alloc(length);
      const { bytesRead } = await handle.read(output, 0, length, start);
      if (bytesRead !== length) throw new Error('Artifact was truncated during transfer.');
      return { descriptor, bytes: output };
    } finally {
      await handle.close();
    }
  }

  async rejectPartial(hash, reason = 'corrupt') {
    const partial = this.partialPath(hash);
    const target = path.join(this.root, 'rejected', `${hash}.${Date.now()}.${reason}.part`);
    await fsp.rename(partial, target).catch(() => {});
    return target;
  }
}

module.exports = { ContentStore, HASH_PATTERN, hashFile };
