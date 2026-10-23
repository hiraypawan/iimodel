'use strict';

const http = require('node:http');
const fsp = require('node:fs/promises');
const path = require('node:path');

const MAX_REQUEST_BYTES = 32 * 1024;
const MAX_PROMPT_CHARACTERS = 4_000;

function sendJson(res, statusCode, payload) {
  const body = JSON.stringify(payload);
  res.writeHead(statusCode, {
    'Content-Type': 'application/json; charset=utf-8',
    'Content-Length': Buffer.byteLength(body),
    'Cache-Control': 'no-store',
    'X-Content-Type-Options': 'nosniff',
  });
  res.end(body);
}

async function readJson(req) {
  let size = 0;
  const chunks = [];
  for await (const chunk of req) {
    size += chunk.length;
    if (size > MAX_REQUEST_BYTES) {
      const error = new Error('Request body is too large.');
      error.statusCode = 413;
      throw error;
    }
    chunks.push(chunk);
  }

  try {
    return JSON.parse(Buffer.concat(chunks).toString('utf8') || '{}');
  } catch {
    const error = new Error('Request body must be valid JSON.');
    error.statusCode = 400;
    throw error;
  }
}

class LocalInferenceServer {
  constructor({ host = '127.0.0.1', port = 7777, expertId = 0 } = {}) {
    this.host = host;
    this.port = port;
    this.expertId = expertId;
    this.server = null;
    this.llama = null;
    this.model = null;
    this.ChatSessionClass = null;
    this.modelPath = null;
    this.loadError = null;
    this.queue = Promise.resolve();
  }

  get loaded() {
    return Boolean(this.model && this.ChatSessionClass);
  }

  get localEndpoint() {
    return `http://${this.host}:${this.port}/infer`;
  }

  status() {
    return {
      ok: true,
      service: 'ii-local-inference',
      expert_id: this.expertId,
      loaded: this.loaded,
      model: this.modelPath ? path.basename(this.modelPath) : null,
      error: this.loadError,
    };
  }

  async start() {
    if (this.server) return this.localEndpoint;
    this.server = http.createServer((req, res) => {
      void this.handleRequest(req, res);
    });

    await new Promise((resolve, reject) => {
      const onError = (error) => {
        this.server?.off('listening', onListening);
        reject(error);
      };
      const onListening = () => {
        this.server?.off('error', onError);
        resolve();
      };
      this.server.once('error', onError);
      this.server.once('listening', onListening);
      this.server.listen(this.port, this.host);
    });
    return this.localEndpoint;
  }

  async disposeRuntime() {
    // Model replacement and shutdown wait for the serialized request queue so
    // a model is never disposed while an isolated job context is using it.
    await this.queue.catch(() => {});
    await this.model?.dispose?.();
    await this.llama?.dispose?.();
    this.ChatSessionClass = null;
    this.model = null;
    this.llama = null;
    this.modelPath = null;
  }

  async loadModel(modelPath) {
    await this.disposeRuntime();
    this.loadError = null;

    if (!modelPath) {
      this.loadError = 'No compatible GGUF model is configured.';
      return false;
    }
    // EXTERNAL GATE: add a runtime-supported distributed expert graph for an exact
    // signed model topology; an isolated safetensors file is not a language model.
    if (path.extname(modelPath).toLowerCase() !== '.gguf') {
      this.loadError = 'node-llama-cpp requires a GGUF model; raw safetensors cannot be executed.';
      return false;
    }

    try {
      await fsp.access(modelPath);
      const { getLlama, LlamaChatSession } = await import('node-llama-cpp');
      this.llama = await getLlama();
      this.model = await this.llama.loadModel({ modelPath });
      this.ChatSessionClass = LlamaChatSession;
      this.modelPath = modelPath;
      return true;
    } catch (error) {
      await this.disposeRuntime();
      this.loadError = error.message;
      return false;
    }
  }

  async infer(prompt, maxTokens, { signal, temperature, seed } = {}) {
    if (!this.loaded) {
      const error = new Error('expert not loaded yet');
      error.statusCode = 503;
      throw error;
    }

    const execute = async () => {
      let context;
      let session;
      try {
        signal?.throwIfAborted?.();
        // Every independent job receives a fresh context sequence and chat
        // session. Model weights remain shared, while KV/conversation state is
        // destroyed after the response to prevent cross-customer leakage.
        context = await this.model.createContext({ contextSize: 2_048 });
        session = new this.ChatSessionClass({
          contextSequence: context.getSequence(),
        });
        const promptOptions = { maxTokens };
        if (signal) promptOptions.signal = signal;
        if (Number.isFinite(temperature)) promptOptions.temperature = temperature;
        if (Number.isInteger(seed)) promptOptions.seed = seed;
        return await session.prompt(prompt, promptOptions);
      } finally {
        await session?.dispose?.();
        await context?.dispose?.();
      }
    };
    const result = this.queue.then(execute, execute);
    this.queue = result.catch(() => {});
    return result;
  }

  async handleRequest(req, res) {
    const url = new URL(req.url || '/', `http://${this.host}:${this.port}`);
    if (req.method === 'GET' && url.pathname === '/health') {
      return sendJson(res, 200, this.status());
    }
    if (req.method !== 'POST' || url.pathname !== '/infer') {
      return sendJson(res, 404, { error: 'not_found', message: 'Use POST /infer.' });
    }

    const contentType = req.headers['content-type'] || '';
    if (!contentType.toLowerCase().startsWith('application/json')) {
      return sendJson(res, 415, {
        error: 'unsupported_media_type',
        message: 'POST /infer requires application/json.',
      });
    }

    try {
      const body = await readJson(req);
      const prompt = typeof body.prompt === 'string' ? body.prompt.trim() : '';
      if (!prompt || prompt.length > MAX_PROMPT_CHARACTERS) {
        return sendJson(res, 400, {
          error: 'invalid_prompt',
          message: `prompt must contain 1–${MAX_PROMPT_CHARACTERS} characters.`,
        });
      }
      const requestedMaxTokens = Number(body.max_tokens ?? body.maxTokens ?? 128);
      const maxTokens = Number.isInteger(requestedMaxTokens)
        ? Math.min(512, Math.max(1, requestedMaxTokens))
        : 128;
      const startedAt = performance.now();
      const text = await this.infer(prompt, maxTokens);
      return sendJson(res, 200, {
        ok: true,
        expert_id: this.expertId,
        text,
        duration_ms: Math.round(performance.now() - startedAt),
        model: path.basename(this.modelPath),
      });
    } catch (error) {
      const statusCode = error.statusCode || 500;
      return sendJson(res, statusCode, {
        error: statusCode === 503 ? 'expert_not_loaded' : 'inference_failed',
        message: statusCode === 503 ? 'expert not loaded yet' : error.message,
      });
    }
  }

  async close() {
    if (this.server) {
      await new Promise((resolve) => this.server.close(resolve));
      this.server = null;
    }
    await this.disposeRuntime();
  }
}

module.exports = { LocalInferenceServer };
