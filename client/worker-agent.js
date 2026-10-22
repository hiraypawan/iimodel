'use strict';

const { signRequest } = require('./host-identity');

class WorkerAgent {
  constructor({
    coordinatorUrl,
    identity,
    inference,
    pollIntervalMs = 3_000,
    requestTimeoutMs = 15_000,
    onState = () => {},
  }) {
    this.coordinatorUrl = coordinatorUrl.replace(/\/$/, '');
    this.identity = identity;
    this.inference = inference;
    this.pollIntervalMs = pollIntervalMs;
    this.requestTimeoutMs = requestTimeoutMs;
    this.onState = onState;
    this.timer = null;
    this.running = false;
    this.stopped = true;
  }

  async post(route, action, payload) {
    const body = signRequest(this.identity, action, payload);
    const response = await fetch(`${this.coordinatorUrl}${route}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(this.requestTimeoutMs),
    });
    if (response.status === 204) return { response, body: null };
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.message || `Coordinator returned HTTP ${response.status}.`);
    return { response, body: result };
  }

  async runOnce() {
    if (this.running || this.stopped || !this.inference?.loaded) return null;
    this.running = true;
    try {
      const leaseResponse = await this.post('/worker/lease', 'worker:lease', {});
      const job = leaseResponse.body?.job;
      if (!job) return null;
      this.onState({ phase: 'working', jobId: job.id, message: 'Running a private background job…' });
      const startedAt = performance.now();
      try {
        const output = await this.inference.infer(job.prompt, job.max_tokens);
        const durationMs = Math.round(performance.now() - startedAt);
        await this.post(`/worker/jobs/${job.id}/complete`, 'worker:complete', {
          lease_token: job.lease_token,
          output,
          metrics: { duration_ms: durationMs },
        });
        this.onState({ phase: 'hosting', jobId: null, message: 'Connected, verified, and ready for jobs' });
        return { id: job.id, status: 'completed', durationMs };
      } catch (error) {
        await this.post(`/worker/jobs/${job.id}/fail`, 'worker:fail', {
          lease_token: job.lease_token,
          error: error.message,
        }).catch(() => {});
        this.onState({ phase: 'hosting', jobId: null, message: 'Job failed safely; waiting for work' });
        return { id: job.id, status: 'failed', error: error.message };
      }
    } finally {
      this.running = false;
    }
  }

  schedule() {
    if (this.stopped) return;
    clearTimeout(this.timer);
    this.timer = setTimeout(async () => {
      try {
        await this.runOnce();
      } catch (error) {
        this.onState({ phase: 'reconnecting', message: 'Job coordinator unavailable — retrying', error: error.message });
      } finally {
        this.schedule();
      }
    }, this.pollIntervalMs);
    this.timer.unref?.();
  }

  start() {
    if (!this.stopped) return;
    this.stopped = false;
    this.schedule();
  }

  stop() {
    this.stopped = true;
    clearTimeout(this.timer);
    this.timer = null;
  }
}

module.exports = { WorkerAgent };
