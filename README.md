# II — Internet Intelligence

> A privacy-conscious background AI worker network with an installable local-first workspace.

[![Coordinator](https://img.shields.io/badge/coordinator-v0.4-caff58?labelColor=111512)](./network)
[![PWA](https://img.shields.io/badge/PWA-installable-70e7d9?labelColor=111512)](./web/app.html)
[![Inference](https://img.shields.io/badge/inference-fail--closed-ffbd6b?labelColor=111512)](./client/inference-server.js)

## Executive truth

II v0.4 is a **software-complete local reference and pilot control plane**. It has signed workers and releases, isolated per-job llama contexts, encrypted durable jobs, capability/load/declared-region scheduling, bounded delayed hedging, deterministic challenges, duplicate comparison, reputation/quarantine, an installable browser PWA, authenticated peer content transfer, a real two-process activation transport reference, a human-gated learning pipeline, and a durable identity/subscription/entitlement backend.

It does **not** bundle or claim a production Qwen checkpoint. It does not prove physical-phone, physical-LAN, multi-NAT, GPU-training, live Dodo, app-store, legal, tax, or financial operation. The peer and activation suites run locally. Browser mobile does not provide a reliable always-on wake word, screen-off microphone, or screen-off local inference path. Those external gates remain explicit.

## What v0.4 implements

### Correctness and privacy

- Every unrelated inference job creates a fresh llama context and chat session. Only immutable model weights stay loaded; session/KV state is disposed in `finally`.
- Prompts, results, teachings, payment webhook payloads, push endpoints, and review notes are AES-256-GCM encrypted with record-specific authenticated context.
- Receipt, lease, identity, checkout-correlation, and API tokens are stored only as hashes.
- The contribution scanner covers direct identifiers, Indian IDs/phone/banking patterns, payment cards, private keys, JWTs, provider tokens, database URLs, password assignments, and high-entropy secrets.
- User, token, plan, checkout, subscription, entitlement, usage, audit, fraud, Genesis qualification, and push-subscription data models are additive SQLite tables.

### Reliable background jobs

- Signed workers declare architecture, memory, GPU hints, concurrency, queue depth, runtime support, benchmark hints, country, and region. These are scheduling hints—not hardware attestation.
- Scheduling checks manifest readiness, current load, declared-region preference, worker quarantine, and manifest-specific decayed score.
- A delayed hedge can create at most **two** active attempts. The winner commits atomically; the coordinator marks the loser cancelled; the desktop agent polls attempt status and passes cancellation to the inference runtime.
- `verification: "duplicate"` requires matching normalized output hashes from two different workers. A conflict fails the job and temporarily quarantines both for review.
- Admin-created deterministic challenges commit only the expected output hash. Pass/fail updates a manifest-specific score; repeated failure quarantines the worker.
- Manifest publication creates per-artifact replica targets (default two replicas across two declared regions); operators can override them and query live signed-artifact deficits.
- Tests inject lease expiry, worker churn, result conflict, local transport interruption, corruption, and a coordinator `SIGKILL`/restart.

### Universal PWA

`web/app.html` is a standards-based PWA for Android Chrome, iOS Safari, and desktop browsers:

- manifest, 192/512 maskable icons, standalone mode, service-worker offline shell;
- PBKDF2-derived AES-256-GCM browser vault for conversations and job receipts;
- no persisted passphrase or API bearer token;
- foreground push-to-talk through the browser speech interface, with captured fallback audio discarded when no transcription adapter exists;
- background job submission, encrypted receipt storage, visible status, completion notifications, service-worker push handling, and safe polling resumption;
- WebGPU, storage, microphone, memory, notification, and local-adapter capability reporting;
- `registerIILocalModelAdapter(adapter)` as a strict foreground adapter boundary—no adapter means local inference is unavailable, never simulated.

The service worker caches only an explicit static shell allowlist. It never caches job, identity, billing, or other API responses.

### Local peer content plane

`p2p/` uses Node 20 built-ins and the existing Ed25519 protocol—no new npm dependency:

- signed announcements and a bootstrap-signed peer directory;
- content-addressed SHA-256 metadata and bounded HTTP byte ranges;
- signed/replay-protected peer requests;
- partial-file resume, final hash verification, corruption quarantine, concurrency and per-peer bandwidth limits;
- authenticated relay and controlled HTTPS/loopback HTTP fallback;
- JSONL reputation observations;
- a test with **three independent peer processes**, an interrupted relayed transfer, replay rejection, oversize-range rejection, HTTP fallback, and corrupt-content rejection.

This is a local reference. It is not evidence of STUN/TURN, public relay capacity, or representative Indian home/mobile NAT traversal.

### Two-node activation reference

`distributed/` starts two real worker processes and sends binary `Float32` activations over authenticated HTTP:

- contiguous layer partitions, bounded frames, SHA-256 input/output verification, replay protection, timeouts, per-stage compute/transport measurements, and p50/p95 harness output;
- deterministic nonlinear reference transforms so output can be compared exactly;
- a worker-kill test that exposes the failed stage and bounded elapsed time.

It is deliberately labelled `deterministic-nonlinear-v1`, `production_model: false`. A physical two-machine Qwen proof still requires an exact licensed checkpoint, tokenizer/topology hashes, compatible distributed runtime, two machines, and published baseline/LAN measurements.

### Reviewed learning flywheel

- encrypted quarantine plus stronger scanning;
- audited plaintext review at `web/review.html`;
- explicit approve/reject/needs-changes decisions and encrypted notes;
- immutable dataset versions with consent/review provenance and content hashes;
- external training-run requests and bounded environment/config metadata;
- benchmark, privacy, artifact hash/size/HTTPS gates;
- separate human approval followed by Ed25519-signed adapter publication;
- staged rollout, rollback, and contributor withdrawal that disables affected releases;
- honest limitation text when published weights cannot be selectively “untrained.”

The coordinator orchestrates metadata and gates only. No GPU trainer is included or claimed.

### Identity and business backend

- passwordless identity challenge model with production delivery-webhook boundary and test-only token return behind `II_AUTH_TEST_MODE=true`;
- scoped, expiring, revocable user tokens;
- INR plan amounts in smallest units (`24900` = ₹249.00; `199900` = ₹1,999.00);
- authenticated checkout correlation with Dodo metadata;
- Standard Webhooks verification (`webhook-id`, timestamp, HMAC signature), replay window, encrypted/idempotent event storage;
- activation, renewal, hold, past-due, pause, cancellation, expiry, refund, and chargeback transitions;
- period quotas and atomic job/token usage accounting;
- cryptographically verified Genesis-host qualification;
- audit records and refund/dispute/correlation fraud flags.

Pre-created Dodo URLs remain a fallback. Live provider validation still needs operator credentials and Dodo test/live webhook deliveries.

## Architecture

```text
Installable browser workspace
┌────────────────────────────────────────────────────────────┐
│ offline shell · encrypted IndexedDB · foreground PTT       │
│ local-adapter boundary OR authenticated background jobs     │
└──────────────────────────────┬─────────────────────────────┘
                               │ HTTPS JSON
Coordinator                    ▼
┌────────────────────────────────────────────────────────────┐
│ Node 20 · Express 4.19 · better-sqlite3 · plain JavaScript │
│ identity/billing · encrypted jobs · scheduler · review      │
└─────────────┬──────────────────────────────┬───────────────┘
              │ signed pull leases           │ signed metadata
              ▼                              ▼
Desktop worker                         Learning/business gates
┌────────────────────────────┐         ┌──────────────────────┐
│ Electron 28 · GGUF llama   │         │ human approval       │
│ isolated context per job   │         │ external evidence    │
│ real result or HTTP 503    │         │ signed publication   │
└────────────────────────────┘         └──────────────────────┘

Local references: authenticated peer chunk plane · two-process activation transport
External gates: exact licensed weights · physical devices/LAN/NAT · GPU trainer · live Dodo
```

The production path remains complete-model background replication. Expert-map rows are placement/routing facts, not proof that a distributed transformer graph exists. `/route` now requires explicit expert IDs; the old prompt-hash pseudo-router is gone.

## Repository layout

```text
network/      Coordinator, reliability, accounts, Dodo, review/training gates
client/       Signed Electron worker and isolated node-llama-cpp inference
protocol/     Dependency-free canonical JSON, Ed25519, and manifests
web/          Landing page, installable PWA, encrypted vault, review console
p2p/          Signed local peer discovery/content/relay reference
distributed/  Binary two-process activation transport and benchmark harness
core/ …       Retained local Python research/tool engine
archive/      Explicitly quarantined experiments
```

## Run the coordinator

Requirements: Node.js 20+, npm, and a random encryption key.

```bash
cd network
npm_config_nodedir=/usr/local npm ci

export II_ADMIN_API_TOKEN="$(node -e "process.stdout.write(require('node:crypto').randomBytes(32).toString('base64url'))")"
export ADMIN_EXPORT_TOKEN="$(node -e "process.stdout.write(require('node:crypto').randomBytes(32).toString('base64url'))")"
export II_JOB_API_TOKEN="$(node -e "process.stdout.write(require('node:crypto').randomBytes(32).toString('base64url'))")"
export II_TEACHING_API_TOKEN="$(node -e "process.stdout.write(require('node:crypto').randomBytes(32).toString('base64url'))")"
export II_DATA_ENCRYPTION_KEY="$(node -e "process.stdout.write(require('node:crypto').randomBytes(32).toString('base64'))")"
npm start
```

Open:

- landing page: `http://localhost:8787/`
- installable workspace: `http://localhost:8787/app.html`
- learning review console: `http://localhost:8787/review.html`

Production identity additionally needs `II_AUTH_DELIVERY_WEBHOOK_URL` and its token. Production billing needs Dodo API/product/webhook configuration. See [`network/.env.example`](./network/.env.example).

## Publish and run a worker model

II does not commit a checkpoint or choose an unreviewed model for the operator.

1. Choose an exact benchmark-verified Qwen-family candidate.
2. Review its license/model-card/commercial redistribution terms.
3. Obtain or reproducibly convert a `node-llama-cpp`-compatible GGUF.
4. Record SHA-256, exact byte size, tokenizer identity, quantization, and benchmark evidence.
5. Generate an offline release key:

```bash
cd network
npm run keygen:release -- ./release-key.pem
```

6. Complete and sign `network/model-manifest.example.json`:

```bash
II_RELEASE_PRIVATE_KEY_PATH=./release-key.pem \
  npm run sign:manifest -- model-manifest.json model-manifest.signed.json
```

7. Publish with `POST /admin/models`, then configure the desktop worker:

```bash
cd client
npm ci
export II_COORDINATOR_URL=http://localhost:8787
export II_RELEASE_PUBLIC_KEY='<trusted publisher key>'
export II_MODEL_MANIFEST_ID='<64-character manifest id>'
export II_ARTIFACT_ID=worker-q4
npm start
```

The worker verifies and resumes the real artifact under `~/.ii/experts/sha256/…`, loads compatible GGUF, reports signed capability/load hints, and polls outbound for work. Unavailable or unloaded inference returns HTTP 503 and does not lease work.

## Submit reliable jobs

A service token still works for operational clients:

```bash
curl -X POST http://localhost:8787/api/jobs \
  -H "Authorization: Bearer $II_JOB_API_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "manifest_id":"MANIFEST_ID",
    "prompt":"Research this topic",
    "max_tokens":256,
    "desired_region":"IN-WEST",
    "hedge_after_ms":2000,
    "verification":"duplicate"
  }'
```

A scoped user token also works and consumes current entitlements atomically. The response contains a one-time job receipt. Status uses that receipt at `GET /api/jobs/:id`.

## Run local peer and activation references

Peer configuration is documented in [`p2p/config.example.json`](./p2p/config.example.json). Start a configured process with:

```bash
node p2p/peer-process.js /path/to/peer-config.json
```

Two activation workers and the harness:

```bash
export II_ACTIVATION_SECRET='<at least 24 random bytes>'
HOST=0.0.0.0 PORT=7801 node distributed/activation-worker.js
HOST=0.0.0.0 PORT=7802 node distributed/activation-worker.js
II_ACTIVATION_NODES=http://127.0.0.1:7801,http://127.0.0.1:7802 \
  node distributed/benchmark.js
```

That benchmark is a transport reference, not a Qwen result.

## Tests

```bash
cd network && npm test && npm run check
cd client && npm test && npm run check
cd web && node --test index.test.js
node --test p2p/p2p.test.js
node --test distributed/distributed.test.js
```

The suites cover privacy/session isolation, signatures/replay, migration, encrypted records, scheduling, hedging/cancellation, duplicate conflicts, challenges/reputation, replica targets, churn/restart, learning gates, signed adapters, contributor withdrawal, identity/quotas, Dodo webhook idempotency/lifecycle, PWA boundaries, three-process peer transfer, resume/corruption/fallback, and two-process activation failure metrics.

## External gates still open

- [ ] Exact licensed worker and local-device Qwen-family checkpoints selected from reproducible benchmarks.
- [ ] Clean-machine Electron installers and native GUI/model smoke tests.
- [ ] Physical Android Chrome, iOS Safari, Windows, and macOS install/capability matrix.
- [ ] Native/offline always-on voice path if product requirements go beyond an open browser.
- [ ] Physical two-machine LAN checkpoint execution with a compatible distributed runtime.
- [ ] Representative NAT/relay tests and operated bootstrap/relay capacity.
- [ ] Real GPU training, benchmark evidence, privacy evaluation, and approved adapter bytes.
- [ ] Dodo sandbox/live deliveries, reconciliation, tax/accounting, incident response, and legal approval.
- [ ] Genesis reward/profit-pool terms and fraud/legal review.
- [ ] Multi-coordinator production database and disaster-recovery proof.

## Non-negotiable rules

1. Never claim a stored expert ID is an executable transformer graph.
2. Never return fabricated inference.
3. Never market local process tests as physical LAN, phone, NAT, GPU, or payment proof.
4. Never load unsigned or hash-mismatched artifacts.
5. Never reuse unrelated customer KV/chat state.
6. Never silently train on chats or auto-merge community adapters.
7. Never put the instant voice reflex behind a random WAN pipeline.

## License

See [LICENSE](./LICENSE). Model checkpoints, payment operation, and user-contributed data have separate rights and obligations; this repository license does not grant permission to redistribute arbitrary weights or training data.
