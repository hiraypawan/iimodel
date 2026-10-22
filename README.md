# II — Internet Intelligence

> A privacy-conscious worker network for background AI jobs, with a local-first Jarvis product as the destination.

[![Coordinator](https://img.shields.io/badge/coordinator-v0.3-c8ff4d?labelColor=111315)](./network)
[![Worker](https://img.shields.io/badge/desktop-signed%20worker-68e8dd?labelColor=111315)](./client)
[![Inference](https://img.shields.io/badge/inference-fail--closed-ffcf66?labelColor=111315)](./client/inference-server.js)

## Executive truth

II v0.3 implements a **cryptographically identified desktop-worker control plane**. It registers signed hosts, accepts only artifacts matching publisher-signed manifests, stores those artifacts as real content-addressed bytes, leases encrypted background jobs to compatible GGUF workers, retries failures, and never fabricates inference.

It is **not yet** a complete phone Jarvis or a distributed 340 GB model. There is no Android/MLC voice app, DHT, WAN tensor pipeline, raw-safetensors expert runtime, automatic LoRA training, or production model bundled in this repository. Those require a reviewed redistributable checkpoint, physical devices, and runtime work documented below.

## What changed in v0.3

- Ed25519 host identities; `device_id` is derived from the public key.
- Signed registration, heartbeat, lease, completion, and failure requests with replay protection.
- Publisher-signed `ii.model-manifest.v1` releases and a trusted release-key boundary.
- Separate desired `device_assignments` from verified `host_artifacts`.
- Coverage counts only live, signed, manifest-matching claims; routable coverage also requires a ready runtime and endpoint.
- Content-addressed SHA-256 artifact paths with resumable HTTP range downloads.
- Durable encrypted jobs, receipt tokens, leases, retries, cancellation, and worker results.
- NAT-friendly pull workers: no inbound public port is required for background jobs.
- Explicit opt-in teaching intake with encryption, direct-identifier/secret rejection, quarantine, status receipts, and deletion.
- Hardened Electron renderer using a sandboxed preload bridge instead of renderer Node access.

## Product architecture

```text
Future phone reflex plane (not shipped)
┌──────────────────────────────────────────────────────────────┐
│ Wake word · local STT · small local model · local TTS       │
│ Instant/private commands stay on the phone                   │
└─────────────────────────────┬────────────────────────────────┘
                              │ hard/background job
                              ▼
Current control plane
┌──────────────────────────────────────────────────────────────┐
│ Node.js 20 · Express 4.19 · SQLite                           │
│ signed identity · signed releases · encrypted jobs · routing │
└─────────────────────────────┬────────────────────────────────┘
                              │ outbound lease polling
                              ▼
Current worker plane
┌──────────────────────────────────────────────────────────────┐
│ Electron 28 · node-llama-cpp · complete compatible GGUF      │
│ verified artifact · real inference or honest HTTP 503        │
└──────────────────────────────────────────────────────────────┘

Research plane (not shipped): runtime-supported LAN pipeline / remote MoE
Distribution future: authenticated peer chunk transfer; HTTP works today
```

The recommended product split is deliberate:

- **Voice/reflex:** local phone execution; never depend on a multi-hop WAN pipeline.
- **Deep work:** stable desktop workers for research, coding, and document jobs.
- **Weight distribution:** signed, content-addressed artifacts; peer transfer later.
- **Distributed tensors:** a measured research track, not a marketing claim.

“One II” means one signed release/protocol bundle. Separate local and worker checkpoints are not Frankenstein inference because their layers are never stitched into one forward pass.

## Corrected sharding status

The original 256-expert, 13-assignment, 20-device math remains a **placement target**:

| Target | Math |
|---|---:|
| Nominal routed expert IDs | 256 |
| Desired assignments per desktop | 13 |
| Desired devices per assignment cycle | `ceil(256 / 13) = 20` |
| 400 placements | 20 target assignment groups |

It is not proof of 20 executable model copies. A real MoE also contains shared attention, embeddings, normalization, routers, KV state, and experts associated with specific layers. Before claiming a full copy, II must publish an exact tensor topology and run one complete forward pass across physical hosts.

The API therefore distinguishes:

- `storedCoveredExperts`: signed manifest artifacts stored by live hosts.
- `coveredExperts`: those claims also marked inference-ready with a routable endpoint.
- `storedFullCopies`: complete stored ID coverage.
- `fullCopies`: complete routable ID coverage.
- `readyWorkers`: complete worker-model replicas ready for pull jobs.

Runtime readiness is currently signed and manifest-matched but still self-attested; remote challenge execution is a future hardening step.

## Security model

### Hosts

On first run, the worker creates an Ed25519 key at `~/.ii/device-key.pem` with mode `0600`. Its device ID is the SHA-256 fingerprint of the public key. Every mutable host claim is signed with a five-minute clock window and monotonically increasing timestamp.

Unsigned hosting is disabled by default. `II_ALLOW_UNSIGNED_HOSTS=true` exists only for local migration and unsigned hosts never satisfy the normal production trust model.

### Releases

A release operator keeps an Ed25519 private release key offline. Coordinators and clients receive only `II_RELEASE_PUBLIC_KEY`. A manifest is rejected if its payload, artifact hash, publisher, or manifest ID changes.

A valid signature proves publisher authorization—not model quality, safety, or legal rights. The manifest additionally requires the operator to confirm redistribution review.

### Jobs and teachings

Prompts, results, and accepted teaching text are AES-256-GCM encrypted in SQLite with `II_DATA_ENCRYPTION_KEY`. Submission tokens and receipt tokens are stored only as SHA-256 hashes. Logs do not print prompt or result text.

The coordinator can decrypt work to lease it to a worker, and the worker sees plaintext while executing it. II does not yet provide confidential computing; users must assume the selected worker can inspect a remote prompt.

See [the network threat model](./docs/NETWORK-THREAT-MODEL.md).

## Repository layout

```text
network/   Coordinator, manifests, encrypted jobs, teaching intake, Dodo
client/    Signed Electron worker, artifact store, llama.cpp runtime
protocol/  Dependency-free canonical signing and manifest protocol
web/       Static landing page
core/ ...  Earlier local research/tool engine retained in the repository
archive/   Quarantined research experiments
```

## Run the coordinator

Requirements: Node.js 20+, npm, and a random encryption key.

```bash
cd network
npm_config_nodedir=/usr/local npm ci   # nodedir is only needed where native headers are unavailable

export II_ADMIN_API_TOKEN="$(node -e "process.stdout.write(require('node:crypto').randomBytes(32).toString('base64url'))")"
export ADMIN_EXPORT_TOKEN="$(node -e "process.stdout.write(require('node:crypto').randomBytes(32).toString('base64url'))")"
export II_JOB_API_TOKEN="$(node -e "process.stdout.write(require('node:crypto').randomBytes(32).toString('base64url'))")"
export II_TEACHING_API_TOKEN="$(node -e "process.stdout.write(require('node:crypto').randomBytes(32).toString('base64url'))")"
export II_DATA_ENCRYPTION_KEY="$(node -e "process.stdout.write(require('node:crypto').randomBytes(32).toString('base64'))")"
npm start
```

The API and landing page default to `http://localhost:8787`.

## Publish a signed worker model

II does not commit model weights or choose an unreviewed production checkpoint for the operator.

1. Review the exact checkpoint license and commercial redistribution terms.
2. Convert or obtain a `node-llama-cpp`-compatible GGUF.
3. Host it over HTTPS.
4. Compute its SHA-256 and exact byte size.
5. Generate an offline release key:

```bash
cd network
npm run keygen:release -- ./release-key.pem
```

6. Put the printed public key in coordinator and client `II_RELEASE_PUBLIC_KEY`.
7. Copy and complete `network/model-manifest.example.json`.
8. Sign it:

```bash
II_RELEASE_PRIVATE_KEY_PATH=./release-key.pem \
  npm run sign:manifest -- model-manifest.json model-manifest.signed.json
```

9. Publish it using the admin token:

```bash
curl -X POST http://localhost:8787/admin/models \
  -H "Authorization: Bearer $II_ADMIN_API_TOKEN" \
  -H "Content-Type: application/json" \
  --data-binary @model-manifest.signed.json
```

10. Set the returned `manifest_id` as `II_MODEL_MANIFEST_ID` on workers. Keep `release-key.pem` offline and out of Git.

## Run a desktop worker

```bash
cd client
npm ci
export II_COORDINATOR_URL=http://localhost:8787
export II_RELEASE_PUBLIC_KEY='<trusted publisher key>'
export II_MODEL_MANIFEST_ID='<64-character manifest id>'
export II_ARTIFACT_ID=worker-q4
npm start
```

The worker:

1. verifies the signed release,
2. resumes/downloads the exact content-addressed artifact,
3. validates its hash and format,
4. loads GGUF through `node-llama-cpp`,
5. registers its signed identity and artifact claim,
6. polls for compatible encrypted background jobs,
7. returns real output or fails/requeues the job.

If loading fails, `/infer` returns HTTP 503 `expert not loaded yet`, and the worker does not lease jobs.

## Submit and read a background job

```bash
SUBMISSION=$(curl -sS -X POST http://localhost:8787/jobs \
  -H "Authorization: Bearer $II_JOB_API_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"manifest_id":"MANIFEST_ID","prompt":"Research this topic","max_tokens":256}')
```

The response contains a one-time `access_token`. Save it; only its hash is stored.

```bash
curl http://localhost:8787/jobs/JOB_ID \
  -H "Authorization: Bearer JOB_ACCESS_TOKEN"
```

Jobs are for background work. This endpoint is not a sub-second voice path.

## Teach II intake

`POST /teachings` is an intake and consent boundary—not automatic training. It requires:

- the exact signed model manifest,
- explicit consent version `2026-10-22`,
- a prompt/correction pair,
- no detected direct identifiers or common secrets,
- `II_TEACHING_API_TOKEN`.

Accepted text is encrypted and quarantined. The contributor receives a receipt token to inspect status or delete it. No code in v0.3 trains, merges, or publishes an adapter automatically.

See [LEARNING-FLYWHEEL.md](./docs/LEARNING-FLYWHEEL.md).

## API overview

| Method | Route | Authentication | Purpose |
|---|---|---|---|
| `POST` | `/register` | Ed25519 host signature | Register identity and exact artifact claims |
| `POST` | `/heartbeat` | Ed25519 host signature | Refresh signed live state |
| `GET` | `/stats` | Public | Truthful stored/routable/worker counts |
| `GET` | `/route?expert_ids=…` | Public | Find ready expert endpoints |
| `GET` | `/models` | Public | List active signed releases |
| `GET` | `/models/:id` | Public | Fetch one signed manifest |
| `POST` | `/admin/models` | Admin bearer | Publish a trusted signed release |
| `POST` | `/admin/hosts/:id/revoke` | Admin bearer | Revoke identity and requeue its lease |
| `POST` | `/jobs` | Job API bearer | Submit encrypted background work |
| `GET` | `/jobs/:id` | Job receipt bearer | Read status/result |
| `POST` | `/jobs/:id/cancel` | Job receipt bearer | Cancel queued/leased work |
| `POST` | `/worker/lease` | Ed25519 host signature | Lease compatible work |
| `POST` | `/worker/jobs/:id/complete` | Host signature + lease token | Return real output |
| `POST` | `/teachings` | Teaching API bearer | Submit consented correction to quarantine |
| `DELETE` | `/teachings/:id` | Teaching receipt bearer | Withdraw and delete contribution |
| `POST` | `/genesis` | Public | Apply for future host verification |
| `GET` | `/admin/genesis` | Admin bearer | Export applications |

## Payments

Dodo checkout and `pricing_mode=by_country` provisioning remain available. Headline prices are `$20 / $10 / ₹249` plus tax. Production still requires provider credentials, signed webhook handling, subscription entitlements, cancellation/refund flows, and final Genesis legal terms. Checkout creation alone is not an entitlement system.

## Tests

```bash
cd network && npm test
cd client && npm test
cd web && node --test index.test.js
```

The suites cover signed/tamper-resistant host messages, trusted manifests, PR #6 migration, assignment math, truthful routing, encryption at rest, job lease/completion, teaching privacy rejection, real resumable 10 MiB allocation, strict inference 503 behavior, Electron worker logic, Dodo contracts, and landing behavior.

## Remaining product milestones

- [ ] Select and legally review exact local-phone and worker checkpoints.
- [ ] Ship Android wake word, offline STT/TTS, and benchmark-selected local model.
- [ ] Implement local/private/background/cloud routing policy.
- [ ] Add authenticated peer chunk transfer, NAT traversal, relays, and HTTP fallback.
- [ ] Add remote readiness challenges and malicious-worker result verification.
- [ ] Prove a two-desktop LAN pipeline with a runtime that really supports model splitting.
- [ ] Define the exact MoE tensor topology before using 256/13/20 as executable capacity.
- [ ] Build benchmark-gated adapter training with PII review, provenance, rollback, and human approval.
- [ ] Add Dodo webhooks and durable subscription entitlements.
- [ ] Publish Genesis eligibility, tax, profit-pool, and jurisdiction terms.
- [ ] Move production coordination from one SQLite process to backed-up, horizontally safe infrastructure.

See [ROADMAP.md](./ROADMAP.md) for dependency order and measurable exit criteria.

## Non-negotiable product rules

1. Never claim an assigned ID is an executable expert.
2. Never return fabricated inference.
3. Never use a WAN multi-host pipeline for the instant voice reflex path.
4. Never load an unsigned or hash-mismatched release.
5. Never auto-train on private chats or auto-merge community adapters.
6. Never describe theoretical storage division as measured compute capacity.

## License

See [LICENSE](./LICENSE). Model checkpoints and user-contributed data have their own licenses and consent requirements; the repository license does not grant rights to redistribute arbitrary weights.
