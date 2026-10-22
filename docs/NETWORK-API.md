# II Network API v0.3

All JSON mutation requests use `Content-Type: application/json`. Error responses have `{error, message}`. Public `/api/...` aliases exist for browser-facing routes where shown in code.

## Host authentication

Host requests include:

```json
{
  "device_id": "ii-<public-key fingerprint>",
  "signed_at": 1792680000000,
  "signature": "base64 Ed25519 signature"
}
```

Registration also includes `public_key`. See `protocol/crypto.js` for canonical signing. Do not implement signing with ordinary `JSON.stringify` order.

## `POST /register`

Action: `host:register`. Rate limit: 10/minute/IP.

Optional operational fields: `deviceName`, `platform`, `clientVersion`, `country`, `memoryGb`, `latencyMs`, `endpoint`. `artifacts` is the complete current claim set; every claim must match an active manifest. An endpoint requires at least one ready claim.

Response includes desired placement under `experts`/`experts_start`/`experts_end`. These are assignments, not verified hosted state.

## `POST /heartbeat`

Action: `host:heartbeat`. Sends status, endpoint, latency, and complete current `artifacts`. Omitted claims are removed. A host cannot select arbitrary expert ranges through heartbeat.

## Artifacts

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

Expert manifests additionally require the exact signed `expert_id`.

## Models

- `GET /models?role=worker`
- `GET /models/:manifestId`
- `POST /admin/models` — `II_ADMIN_API_TOKEN`
- `POST /admin/models/:manifestId/deactivate` — `II_ADMIN_API_TOKEN`
- `POST /admin/hosts/:deviceId/revoke` — `II_ADMIN_API_TOKEN`; requires JSON `reason`

Deactivation prevents new routing/leasing and fails pending work for that release.

## Jobs

### Submit

`POST /jobs`, bearer `II_JOB_API_TOKEN`:

```json
{
  "manifest_id": "active worker manifest",
  "prompt": "background task",
  "max_tokens": 256,
  "max_attempts": 3
}
```

Returns HTTP 202 with job ID and one-time access token.

### Status/cancel

- `GET /jobs/:id` — bearer receipt token.
- `POST /jobs/:id/cancel` — bearer receipt token.

### Worker lease

`POST /worker/lease`, action `worker:lease`. Returns HTTP 204 when no compatible job exists, otherwise plaintext prompt plus a one-time lease token. The host must have a live, ready claim for the exact manifest.

### Complete/fail

- `POST /worker/jobs/:id/complete`, action `worker:complete`.
- `POST /worker/jobs/:id/fail`, action `worker:fail`.

Both include `lease_token`. Completion includes `output` and optional bounded metrics. Failure includes a bounded error. Expired or wrong-worker leases are rejected.

## Teachings

### Submit

`POST /teachings`, bearer `II_TEACHING_API_TOKEN`:

```json
{
  "manifest_id": "exact active release",
  "prompt": "previous input",
  "correction": "user-approved better response",
  "language": "mr-IN",
  "category": "education",
  "consent": true,
  "consent_version": "2026-10-22"
}
```

Sensitive-pattern findings return HTTP 422 and are not stored. Accepted intake returns a receipt and remains quarantined.

- `GET /teachings/:id` — receipt token; metadata only.
- `DELETE /teachings/:id` — receipt token; permanent row deletion.
- `GET /admin/teachings` — admin bearer; metadata only, never content.

## Public stats/routing

- `GET /stats` reports signed/manifest-backed state and job counts.
- `GET /route?expert_ids=0,1` returns direct expert endpoints only when live and ready.

Prompt-hash expert selection remains a research placeholder. Production background work uses `/jobs`, not `/route`.

## Genesis and checkout

- `POST /genesis`
- `GET /admin/genesis` — admin bearer CSV.
- `GET /api/checkout/monthly|yearly`

Checkout redirects do not by themselves create subscription entitlements.
