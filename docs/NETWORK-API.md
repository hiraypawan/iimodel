# II Network API v0.4

JSON mutations use `Content-Type: application/json`. Errors use `{ "error": "code", "message": "…" }` where a safe message is available. Browser-facing aliases generally live under `/api`; exact route arrays are the source of truth.

## Authentication classes

### Signed host

Mutable host/worker requests include:

```json
{
  "device_id": "ii-<Ed25519 SPKI SHA-256 prefix>",
  "signed_at": 1792680000000,
  "signature": "base64 Ed25519 signature"
}
```

Registration also includes `public_key`. Sign canonical payloads through `protocol/crypto.js`; ordinary property-order `JSON.stringify` is not the protocol. Signatures bind an action such as `host:heartbeat` or `worker:complete`. Timestamps have a five-minute window and must increase per device.

### Service/admin/user bearer

- `II_ADMIN_API_TOKEN`: release, review, reliability, business, fraud, and Genesis administration.
- `II_JOB_API_TOKEN`: service-to-service job submission.
- `II_TEACHING_API_TOKEN`: consented teaching intake.
- user tokens: hashed, scoped, expiring, and revocable; a `jobs:submit` token may submit against active quota.
- receipt/lease tokens: one-record capability tokens stored only as hashes.

## Identity

### `POST /api/auth/request-link`

Body: `{ "email": "person@example.com" }`.

Production requires `II_AUTH_DELIVERY_WEBHOOK_URL` and `II_AUTH_DELIVERY_WEBHOOK_TOKEN`. Login tokens are sent to that delivery adapter and not returned. `II_AUTH_TEST_MODE=true` returns `test_only_token` solely for automated/local tests.

### `POST /api/auth/verify`

Body: `{ "challenge_id": "identity_…", "token": "…" }`. Consumes the one-time challenge, verifies/creates the user, grants a bounded free period, and returns a scoped access token once.

### User routes

- `GET /api/me` — profile, subscription metadata, current entitlements, Genesis qualification.
- `POST /api/tokens` — issue a token with a subset of current scopes.
- `DELETE /api/tokens/:id` — revoke one owned token.
- `POST /api/push-subscriptions` — store HTTPS endpoint/keys encrypted; requires a configured external sender for actual push delivery.
- `GET /api/plans` — public plan amounts; amounts are smallest currency units.

## Hosts and capabilities

### `POST /register`

Action `host:register`; limited to 10 registrations/IP/minute. Optional operational fields include name, platform, client version, country, memory, endpoint, and signed `capabilities`:

```json
{
  "region": "IN-WEST",
  "architecture": "win32-x64",
  "cpu_cores": 8,
  "memory_mb": 16384,
  "gpu_backend": "cuda",
  "gpu_name": "operator-declared name",
  "vram_mb": 8192,
  "max_concurrency": 1,
  "queue_depth": 0,
  "supported_runtimes": ["node-llama-cpp@3.5.0"],
  "benchmark": {
    "tokens_per_second": 12.4,
    "time_to_first_token_ms": 750,
    "sample_count": 5,
    "manifest_id": "64 hex"
  }
}
```

These fields are self-declared hints—not remote attestation. `artifacts` is the complete current manifest-backed claim set. A public endpoint requires a ready claim.

### `POST /heartbeat`

Action `host:heartbeat`. Refreshes status, endpoint, latency, capabilities/load, and the complete artifact claim set. Omitted claims are removed.

### Artifact claim

```json
{
  "manifest_id": "64 hex",
  "artifact_id": "worker-q4",
  "sha256": "64 hex",
  "size_bytes": 123,
  "format": "gguf",
  "inference_ready": true
}
```

Expert-role artifacts also require the exact signed `expert_id`.

## Signed model releases

- `GET /models?role=worker`
- `GET /models/:manifestId`
- `POST /admin/models`
- `POST /admin/models/:manifestId/deactivate`
- `POST /admin/hosts/:deviceId/revoke` with bounded `reason`

Deactivation blocks routing/leasing and fails pending work for that release. A valid signature proves publisher authorization, not model quality/license safety.

## Jobs and reliability

### Submit `POST /jobs` or `/api/jobs`

Authentication: service job token or scoped user token.

```json
{
  "manifest_id": "active worker manifest",
  "prompt": "background task",
  "max_tokens": 256,
  "max_attempts": 3,
  "desired_region": "IN-WEST",
  "hedge_after_ms": 2000,
  "verification": "duplicate"
}
```

`verification` is omitted for first-valid-result behavior. `duplicate` requires two matching normalized SHA-256 outputs from distinct workers. Hedging delay is 250–60,000 ms; no job has more than two parallel attempts. User submissions atomically consume active job/token entitlements.

Returns HTTP 202 with a one-time job receipt.

### Receipt routes

- `GET /jobs/:id` — status, result, comparison state, and aggregate attempt counts.
- `POST /jobs/:id/cancel` — marks every active attempt cancelled.

### Lease `POST /worker/lease`

Action `worker:lease`. Signed capabilities can refresh lease-time load. Returns 204 or:

```json
{
  "job": {
    "id": "job_…",
    "attempt_id": "attempt_…",
    "is_hedge": false,
    "kind": "inference",
    "manifest_id": "…",
    "prompt": "plaintext visible to selected worker",
    "max_tokens": 256,
    "lease_token": "one-time secret",
    "lease_expires_at": "ISO date"
  }
}
```

Selection requires exact ready artifact, capacity, no quarantine, and honours declared-region preference when a matching live worker has capacity. Scores are manifest-specific and decay toward neutral.

### Attempt cancellation/status

`POST /worker/attempts/:id/status`, action `worker:attempt-status`, with attempt and lease token. The desktop agent polls this during generation and aborts when the coordinator cancels a losing hedge.

### Complete/fail

- `POST /worker/jobs/:id/complete`, action `worker:complete`;
- `POST /worker/jobs/:id/fail`, action `worker:fail`.

Include `attempt_id` and `lease_token`. Completion includes bounded output and an allowlisted numeric metrics object. Arbitrary metric keys are discarded to avoid accidental secret/PII persistence.

### Challenges/reputation/replicas

- `POST /admin/challenges` — prompt, expected output, active manifest; only expected normalized SHA-256 is retained.
- `GET /admin/reliability/workers` — score/counters/quarantine and capability hints.
- `POST /admin/reliability/workers/:device/:manifest/release` — clear reviewed quarantine.
- `GET /admin/reliability/conflicts` — output hashes and worker IDs, not plaintext.
- `PUT /admin/reliability/replicas/:manifest/:artifact` — target replicas/regions.
- `GET /reliability/replicas` — live deficits from signed artifact claims and declared regions.

## Public routing/stats

- `GET /stats` — signed/manifest-backed host, storage, ready-worker, job, and Genesis counts.
- `GET /route?expert_ids=0,1` — explicit expert IDs only; live ready SQLite claims.

There is no prompt-hash pseudo-router. Expert maps still do not prove a distributed model graph.

## Teaching, review, datasets, and adapters

### Contributor

- `POST /teachings` — teaching token, exact manifest, bounded prompt/correction, category/language, explicit consent/version.
- `GET /teachings/:id` — receipt; metadata only.
- `DELETE /teachings/:id` — receipt; tombstones encrypted content, records affected derivations, and withdraws affected staged/active releases.

Privacy findings return 422 and content is not stored.

### Review and dataset admin

- `GET /admin/teachings` — metadata only.
- `GET /admin/teachings/:id/content` — audited decrypted view.
- `POST /admin/teachings/:id/review` — `approved`, `rejected`, or `needs_changes` plus reviewer/notes.
- `POST /admin/datasets` — approved IDs from one exact manifest.
- `POST /admin/datasets/:id/seal` — immutable item/provenance hash.
- `GET /admin/datasets`

### External training and publication

- `POST /admin/training-runs` — sealed dataset and bounded secret-scanned metadata.
- `POST /admin/training-runs/:id/report` — external status/evidence.
- `POST /admin/training-runs/:id/approve` — separate human gate.
- `POST /admin/training-runs/:id/publish` — canonical Ed25519 adapter release; requires `II_ADAPTER_SIGNING_PRIVATE_KEY`.
- `GET /admin/training-runs`
- `POST /admin/adapters/:id/rollout`
- `POST /admin/adapters/:id/rollback`

No route runs a GPU trainer.

## Dodo billing and entitlements

### Checkout

- `GET /api/checkout/monthly|yearly` — anonymous redirect/legacy link path.
- `POST /api/billing/checkout` — authenticated and correlated; body `{ "plan": "monthly" }`.
- `GET /api/billing/status` — owned checkout/subscription metadata.

Authenticated API-created checkouts include opaque local checkout/user/correlation metadata. Pre-created URLs remain a fallback but must be configured to return equivalent metadata before entitlement automation can correlate them.

### `POST /webhooks/dodo`

Requires `DODO_PAYMENTS_WEBHOOK_KEY` and Standard Webhooks headers:

```text
webhook-id
webhook-timestamp
webhook-signature: v1,<base64 HMAC-SHA256>
```

The signature covers the exact raw body and must be within five minutes. Each event ID/payload hash is idempotently stored; payloads are encrypted. Reusing an event ID with another body raises a critical fraud flag.

Handled lifecycle includes active/renewed/hold/past-due/paused/unpaused/cancelled/failed/expired, refund success, and dispute/chargeback states. Active/renewed events grant period entitlements; adverse terminal states suspend/expire them.

- `GET /admin/billing/events` — metadata/audit/fraud flags, no plaintext payload.
- `POST /admin/billing/fraud/:id/resolve`

## Genesis

- `POST /genesis` — public host-interest intake.
- `GET /admin/genesis` — protected CSV export.
- `POST /admin/genesis/qualify` — links a real user to a cryptographically verified first-400 artifact host.

A signup or desired assignment alone never qualifies.
