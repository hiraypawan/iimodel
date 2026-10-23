# II Network and Product Architecture

## Design decision

II separates:

1. an installable local browser workspace;
2. a durable background control plane;
3. complete-model desktop workers;
4. reviewed learning/business state machines;
5. local peer and activation research references.

Complete compatible GGUF replicas are the current executable worker unit. Expert IDs remain placement/routing metadata until an exact distributed topology runs on physical machines.

## Coordinator modules

`network/server.js` initializes schema/migrations and composes bounded modules instead of containing each subsystem inline:

| Module | Responsibility |
|---|---|
| `model-registry.js` | publisher-signed model release publication/deactivation |
| `security.js` | bearer, host signature/replay, AES-GCM record stores, token hashing |
| `accounts.js` | users, passwordless identity delivery boundary, scoped tokens, plans/entitlements, Genesis qualification |
| `billing.js` | correlated Dodo checkout, raw Standard Webhooks verification, lifecycle/idempotency/fraud |
| `jobs.js` | encrypted jobs, attempts, leases, hedges, comparison, challenge completion |
| `reliability.js` | capability normalization, score decay, quarantine, replica targets |
| `privacy.js` | deterministic PII/secret/card/entropy scan |
| `learning.js` | intake, audited review, datasets, external-run metadata, signed adapters, withdrawal |

SQLite provides single-process pilot durability and WAL recovery. It is not presented as a horizontally safe public database.

## Important schema separations

| State | Meaning |
|---|---|
| `devices` | signed identity/liveness/endpoint |
| `worker_capabilities` | signed but self-declared hardware/load/region hints |
| `device_assignments` | desired expert placement; never capacity truth |
| `host_artifacts` | exact active-manifest artifacts claimed by a host |
| `worker_manifest_scores` | observations scoped to device + exact model manifest |
| `jobs` | encrypted logical work/result/receipt/comparison policy |
| `job_attempts` | individual worker leases, outputs, hedge/cancel/expiry state |
| `replica_targets` | operator target, not observed capacity |
| identity/billing tables | users, hashed tokens, checkout correlation, subscription/entitlement/usage/audit/fraud |
| learning tables | encrypted teachings/reviews, immutable datasets, training metadata, signed release state |

## Worker lifecycle and privacy

```text
Create/load Ed25519 identity
  → fetch and verify publisher-signed manifest
  → resume exact content-addressed artifact
  → verify SHA-256, byte size, and structure
  → load a complete compatible GGUF
  → sign registration, artifact, capabilities, and region/load hints
  → heartbeat every 30 seconds
  → pull a manifest-compatible attempt
  → create a fresh llama context + chat session
  → infer or fail closed
  → dispose session/context in finally
  → sign completion/failure
```

Model weights may remain loaded. Chat/KV state never survives an unrelated job. Failed load means `inference_ready=false`, no leases, and loopback HTTP 503.

## Job and attempt state

Logical jobs retain the existing durable states:

```text
queued ↔ leased → completed
   │        │         ▲
   │        ├─ failure/expiry → queued or failed
   │        ├─ delayed hedge (at most 2 active attempts)
   │        └─ duplicate conflict → failed + temporary quarantine
   └──────────────→ cancelled
```

Each lease is a separate `job_attempts` row with its own hashed lease token. On first-valid completion, the transaction commits one result and marks all other active attempts cancelled. Workers poll signed attempt status and pass cancellation to inference. A late loser cannot overwrite the result.

Duplicate verification waits for two different completed workers and compares normalized output hashes. A mismatch stores encrypted attempt outputs for controlled investigation but exposes only hashes in the conflict API.

## Scheduling and reputation

A pulling worker is eligible only when:

- it has an exact live inference-ready artifact claim;
- its signed queue depth and active coordinator attempts are below concurrency;
- the manifest-specific score is not quarantined;
- it has not already produced/actively run the same job;
- the logical attempt/parallel bounds permit another lease.

A requested declared region gets first refusal while a matching live worker has capacity. Region is user/worker-declared and is not geolocation attestation.

Scores decay toward neutral over seven days. Success, failure, lease expiry, deterministic challenge, duplicate conflict, and peer observations are distinct event classes. Conflict quarantines both workers briefly because the coordinator cannot identify the liar from two outputs alone. Admin review can release quarantine.

## PWA

The static PWA has no framework/build dependency. It uses:

- service worker with an exact shell cache allowlist;
- Web Crypto PBKDF2 + AES-GCM vault in IndexedDB;
- tab-only coordinator token;
- foreground speech/local-adapter boundary;
- receipt-encrypted background polling and notifications.

It never caches authenticated API responses. Browser suspension means status polling and local inference are best-effort while open; actual background delivery requires Web Push deployment.

## Peer content reference

`p2p/` is a separate authenticated artifact data plane:

```text
signed peer announcement → bootstrap-signed lookup
  → signed bounded byte ranges (direct or authenticated relay)
  → partial-file fsync/resume
  → final SHA-256 verification
  → atomic content-addressed publication or corruption quarantine
  → reputation observation
```

This local HTTP reference has bootstrap/relay configuration and controlled HTTPS fallback. It has no claim of public NAT traversal.

## Activation reference

`distributed/` sends bounded binary activations between two independent local worker processes and measures failure. It deliberately computes a deterministic synthetic transform. It is a transport/protocol harness, not a model runtime.

## Business and learning gates

Dodo provider payloads are signature-checked against their exact raw bytes, encrypted, and idempotently stored before state transition. Entitlements derive only from correlated provider events—not redirects or browser claims.

Teachings move through explicit consent, scan, encrypted quarantine, audited review, immutable dataset, external-run evidence, benchmark/privacy gates, human approval, signature, rollout, and rollback. No transition invokes a GPU trainer automatically.

## Retained Python engine

The earlier local/BYOK tool engine under `core/`, `reasoning/`, and related packages is independent of this Node protocol. Its historical design remains in `docs/ARCHITECTURE.md` and must not be read as a distributed-worker implementation.
