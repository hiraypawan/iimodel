'use strict';

const { signRequest } = require('./host-identity');

class WorkerAgent {
  constructor({
    coordinatorUrl,
    identity,
    inference,
    pollIntervalMs = 3_000,
    requestTimeoutMs = 15_000,
    cancellationPollMs = 1_000,
    capabilities = () => ({}),
    onState = () => {},
  }) {
    this.coordinatorUrl = coordinatorUrl.replace(/\/$/, '');
    this.identity = identity;
    this.inference = inference;
    this.pollIntervalMs = pollIntervalMs;
    this.requestTimeoutMs = requestTimeoutMs;
    this.cancellationPollMs = cancellationPollMs;
    this.capabilities = capabilities;
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
      const leaseResponse = await this.post('/worker/lease', 'worker:lease', {
        capabilities: this.capabilities(),
      });
      const job = leaseResponse.body?.job;
      if (!job) return null;
      this.onState({
        phase: 'working',
        jobId: job.id,
        attemptId: job.attempt_id,
        message: job.is_hedge ? 'Running a delayed backup attempt…' : 'Running an isolated background job…',
      });
      const startedAt = performance.now();
      const controller = new AbortController();
      let cancellationTimer;
      let cancelledByCoordinator = false;
      const pollCancellation = async () => {
        try {
          const status = await this.post(
            `/worker/attempts/${job.attempt_id}/status`,
            'worker:attempt-status',
            { attempt_id: job.attempt_id, lease_token: job.lease_token },
          );
          if (status.body?.cancel_requested || status.body?.status !== 'active') {
            cancelledByCoordinator = status.body?.status === 'cancelled';
            controller.abort(new Error('Attempt cancelled by coordinator.'));
          }
        } catch {
          // A transient status-check failure must not discard a valid lease.
        }
      };
      cancellationTimer = setInterval(() => void pollCancellation(), this.cancellationPollMs);
      cancellationTimer.unref?.();
      try {
        const output = await this.inference.infer(job.prompt, job.max_tokens, {
          signal: controller.signal,
          temperature: job.temperature,
          seed: job.seed,
        });
        if (cancelledByCoordinator) {
          return { id: job.id, attemptId: job.attempt_id, status: 'cancelled' };
        }
        const durationMs = Math.round(performance.now() - startedAt);
        const completed = await this.post(`/worker/jobs/${job.id}/complete`, 'worker:complete', {
          attempt_id: job.attempt_id,
          lease_token: job.lease_token,
          output,
          metrics: { duration_ms: durationMs },
        });
        const status = completed.body?.status || 'completed';
        this.onState({ phase: 'hosting', jobId: null, attemptId: null, message: 'Connected, verified, and ready for jobs' });
        return { id: job.id, attemptId: job.attempt_id, status, durationMs };
      } catch (error) {
        if (!cancelledByCoordinator) {
          await this.post(`/worker/jobs/${job.id}/fail`, 'worker:fail', {
            attempt_id: job.attempt_id,
            lease_token: job.lease_token,
            error: error.message,
          }).catch(() => {});
        }
        this.onState({
          phase: 'hosting',
          jobId: null,
          attemptId: null,
          message: cancelledByCoordinator
            ? 'Backup attempt cancelled after another worker completed'
            : 'Job failed safely; waiting for work',
        });
        return {
          id: job.id,
          attemptId: job.attempt_id,
          status: cancelledByCoordinator ? 'cancelled' : 'failed',
          error: error.message,
        };
      } finally {
        clearInterval(cancellationTimer);
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
