# II Decentralized Product Architecture

## Design decision

II uses a local reflex plane and a background worker plane. Complete-model replicas are the current production unit. Distributed tensor execution and mobile hosting are research tracks until measured on physical hardware.

## Control plane

`network/server.js` owns public registration/stats/routing and composes three bounded modules:

- `model-registry.js`: trusted signed releases,
- `jobs.js`: encrypted durable work and worker leases,
- `learning.js`: consented encrypted correction quarantine.

SQLite provides pilot durability. Schema tables intentionally separate concerns:

| Table | Meaning |
|---|---|
| `devices` | signed identity, liveness, endpoint, operational metadata |
| `device_assignments` | desired expert placement; never capacity truth |
| `model_manifests` | active trusted publisher releases |
| `host_artifacts` | exact manifest-backed artifacts claimed by a host |
| `device_experts` | derived hosted expert IDs for compatibility/introspection |
| `jobs` | encrypted prompt/result, lease, retry, receipt state |
| `teachings` | encrypted quarantined corrections and consent metadata |
| `genesis` | minimal applicant email/country record |

## Worker lifecycle

```text
Create/load Ed25519 identity
  → fetch signed manifest
  → verify configured publisher key
  → select artifact
  → resume content-addressed download
  → verify exact SHA-256, size, and structure
  → load complete GGUF where supported
  → sign registration and artifact claim
  → sign heartbeat every 30 seconds
  → pull matching job lease
  → run real local inference
  → sign completion/failure
```

A failed model load means `inference_ready=false`, no job leases, and HTTP 503 from the loopback inference endpoint.

## Job state machine

```text
queued → leased → completed
   ▲        │
   │        ├─ explicit worker failure → queued or failed
   │        └─ lease expiry           → queued or failed
   └────────────────────────────────────────────────────
queued/leased → cancelled (receipt owner)
```

Prompts/results are encrypted at rest. A job access token is returned once; only its hash remains. A separate one-time lease token prevents another registered host from completing the job.

## Host authentication message

```text
ii-host-auth-v1
<action>
<signed_at milliseconds>
<canonical JSON body without auth envelope>
```

Actions are distinct (`host:register`, `host:heartbeat`, `worker:lease`, `worker:complete`, `worker:fail`) to prevent cross-endpoint signature reuse.

## Capacity semantics

Assignments answer “what should this host obtain?” Host artifacts answer “what official bytes does it claim to hold?” Runtime readiness answers “what does it claim it can execute?” These states are never collapsed.

The coordinator still cannot prove execution correctness. Future challenge jobs and reputation must establish observed behavior.

## Network transport

Background work uses worker-initiated polling, which works behind ordinary NAT without inbound ports. Direct expert routing remains experimental and requires an operator-provided HTTPS endpoint. Future peer artifact transfer needs authenticated identities, NAT traversal, relay fallback, and signed chunk maps.

## Local Python engine

The repository also retains an earlier local/BYOK tool engine under `core/`, `reasoning/`, and related packages. It is independent of the Node worker protocol. Its historical architecture remains in `docs/ARCHITECTURE.md`; do not interpret that document as a distributed-worker implementation.
