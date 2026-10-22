'use strict';

const fs = require('node:fs');
const fsp = require('node:fs/promises');
const path = require('node:path');
const crypto = require('node:crypto');

const DEFAULT_MAX_EXPERT_BYTES = 2 * 1024 * 1024 * 1024;
const MAX_SAFETENSORS_HEADER_BYTES = 16 * 1024 * 1024;

function expertsDirectory(homeDirectory) {
  return path.join(homeDirectory, '.ii', 'experts');
}

function normalizeSourceUrl(value) {
  let parsed;
  try {
    parsed = new URL(value);
  } catch {
    throw new Error('II_EXPERT_SOURCE_URL must be a valid HTTP or HTTPS URL.');
  }

  if (!['http:', 'https:'].includes(parsed.protocol)) {
    throw new Error('II_EXPERT_SOURCE_URL must use HTTP or HTTPS.');
  }
  if (parsed.protocol === 'http:' && !['127.0.0.1', 'localhost', '::1'].includes(parsed.hostname)) {
    throw new Error('Expert downloads must use HTTPS unless the source is localhost.');
  }
  return parsed;
}

function artifactExtension(sourceUrl) {
  const extension = path.extname(sourceUrl.pathname).toLowerCase();
  return extension === '.gguf' ? '.gguf' : '.safetensors';
}

async function sha256File(filePath) {
  const hash = crypto.createHash('sha256');
  await new Promise((resolve, reject) => {
    const input = fs.createReadStream(filePath);
    input.on('data', (chunk) => hash.update(chunk));
    input.on('error', reject);
    input.on('end', resolve);
  });
  return hash.digest('hex');
}

async function validateSafetensors(filePath) {
  const handle = await fsp.open(filePath, 'r');
  try {
    const stats = await handle.stat();
    if (stats.size < 10) throw new Error('Safetensors artifact is too small.');

    const prefix = Buffer.alloc(8);
    const prefixRead = await handle.read(prefix, 0, prefix.length, 0);
    if (prefixRead.bytesRead !== prefix.length) throw new Error('Safetensors header is truncated.');

    const headerLengthBigInt = prefix.readBigUInt64LE(0);
    if (headerLengthBigInt < 2n || headerLengthBigInt > BigInt(MAX_SAFETENSORS_HEADER_BYTES)) {
      throw new Error('Safetensors header length is invalid.');
    }
    const headerLength = Number(headerLengthBigInt);
    if (8 + headerLength >= stats.size) throw new Error('Safetensors tensor data is missing.');

    const headerBuffer = Buffer.alloc(headerLength);
    const headerRead = await handle.read(headerBuffer, 0, headerLength, 8);
    if (headerRead.bytesRead !== headerLength) throw new Error('Safetensors header is truncated.');

    let header;
    try {
      header = JSON.parse(headerBuffer.toString('utf8').trim());
    } catch {
      throw new Error('Safetensors header is not valid JSON.');
    }

    const tensors = Object.entries(header).filter(([name]) => name !== '__metadata__');
    if (!tensors.length) throw new Error('Safetensors artifact contains no tensors.');
    const dataSize = stats.size - 8 - headerLength;
    for (const [name, tensor] of tensors) {
      if (!tensor || !Array.isArray(tensor.data_offsets) || tensor.data_offsets.length !== 2) {
        throw new Error(`Tensor ${name} has invalid data offsets.`);
      }
      const [start, end] = tensor.data_offsets;
      if (!Number.isInteger(start) || !Number.isInteger(end)
        || start < 0 || end <= start || end > dataSize) {
        throw new Error(`Tensor ${name} points outside the artifact.`);
      }
    }

    return { format: 'safetensors', tensorCount: tensors.length, sizeBytes: stats.size };
  } finally {
    await handle.close();
  }
}

async function validateGguf(filePath) {
  const handle = await fsp.open(filePath, 'r');
  try {
    const stats = await handle.stat();
    const magic = Buffer.alloc(4);
    const result = await handle.read(magic, 0, magic.length, 0);
    if (result.bytesRead !== 4 || magic.toString('ascii') !== 'GGUF') {
      throw new Error('GGUF artifact has an invalid file signature.');
    }
    return { format: 'gguf', tensorCount: null, sizeBytes: stats.size };
  } finally {
    await handle.close();
  }
}

async function validateArtifact(filePath) {
  return path.extname(filePath).toLowerCase() === '.gguf'
    ? validateGguf(filePath)
    : validateSafetensors(filePath);
}

async function downloadExpert({
  homeDirectory,
  expertId = 0,
  sourceUrl,
  expectedSha256 = '',
  maxBytes = DEFAULT_MAX_EXPERT_BYTES,
  onProgress = () => {},
}) {
  if (!Number.isInteger(expertId) || expertId < 0 || expertId > 255) {
    throw new Error('II_EXPERT_ID must be an integer from 0 to 255.');
  }
  if (!Number.isSafeInteger(maxBytes) || maxBytes < 1) {
    throw new Error('II_EXPERT_MAX_BYTES must be a positive integer.');
  }

  const parsedSource = normalizeSourceUrl(sourceUrl);
  const expectedHash = String(expectedSha256 || '').trim().toLowerCase();
  if (expectedHash && !/^[a-f0-9]{64}$/.test(expectedHash)) {
    throw new Error('II_EXPERT_SHA256 must contain exactly 64 hexadecimal characters.');
  }

  const directory = expertsDirectory(homeDirectory);
  await fsp.mkdir(directory, { recursive: true, mode: 0o700 });
  const destination = path.join(directory, `expert-${expertId}${artifactExtension(parsedSource)}`);
  const partial = `${destination}.part`;

  try {
    const existing = await fsp.stat(destination);
    if (existing.isFile() && existing.size > 0) {
      const actualHash = await sha256File(destination);
      if (!expectedHash || actualHash === expectedHash) {
        const validation = await validateArtifact(destination);
        return {
          expertId,
          path: destination,
          sha256: actualHash,
          downloaded: false,
          ...validation,
        };
      }
    }
  } catch (error) {
    if (error.code !== 'ENOENT') {
      await fsp.rm(destination, { force: true });
    }
  }

  await fsp.rm(partial, { force: true });
  const timeoutMs = Number.parseInt(process.env.II_EXPERT_DOWNLOAD_TIMEOUT_MS || '1800000', 10);
  if (!Number.isInteger(timeoutMs) || timeoutMs < 1_000) {
    throw new Error('II_EXPERT_DOWNLOAD_TIMEOUT_MS must be an integer of at least 1000.');
  }
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);

  let handle;
  try {
    const response = await fetch(parsedSource, { signal: controller.signal, redirect: 'follow' });
    if (!response.ok || !response.body) {
      throw new Error(`Expert download returned HTTP ${response.status}.`);
    }

    const contentLength = Number(response.headers.get('content-length'));
    if (Number.isFinite(contentLength) && contentLength > maxBytes) {
      throw new Error(`Expert artifact exceeds the ${maxBytes}-byte download limit.`);
    }

    handle = await fsp.open(partial, 'wx', 0o600);
    const hash = crypto.createHash('sha256');
    let bytesWritten = 0;
    for await (const chunk of response.body) {
      const data = Buffer.from(chunk);
      bytesWritten += data.length;
      if (bytesWritten > maxBytes) {
        throw new Error(`Expert artifact exceeds the ${maxBytes}-byte download limit.`);
      }
      hash.update(data);
      await handle.write(data);
      onProgress({ bytesWritten, contentLength: Number.isFinite(contentLength) ? contentLength : null });
    }
    await handle.sync();
    await handle.close();
    handle = null;

    if (bytesWritten === 0) throw new Error('Expert download returned an empty artifact.');
    const actualHash = hash.digest('hex');
    if (expectedHash && actualHash !== expectedHash) {
      throw new Error(`Expert checksum mismatch: expected ${expectedHash}, received ${actualHash}.`);
    }

    const validation = await validateArtifact(partial);
    await fsp.rm(destination, { force: true });
    await fsp.rename(partial, destination);
    await fsp.chmod(destination, 0o600);
    await fsp.writeFile(
      path.join(directory, `expert-${expertId}.json`),
      `${JSON.stringify({
        expertId,
        sourceUrl: parsedSource.toString(),
        sha256: actualHash,
        sizeBytes: validation.sizeBytes,
        format: validation.format,
        downloadedAt: new Date().toISOString(),
      }, null, 2)}\n`,
      { mode: 0o600 },
    );

    return {
      expertId,
      path: destination,
      sha256: actualHash,
      downloaded: true,
      ...validation,
    };
  } catch (error) {
    if (error.name === 'AbortError') {
      throw new Error(`Expert download exceeded ${Math.round(timeoutMs / 1000)} seconds.`);
    }
    throw error;
  } finally {
    clearTimeout(timeout);
    if (handle) await handle.close().catch(() => {});
    await fsp.rm(partial, { force: true }).catch(() => {});
  }
}

module.exports = {
  DEFAULT_MAX_EXPERT_BYTES,
  downloadExpert,
  expertsDirectory,
  sha256File,
  validateArtifact,
};
