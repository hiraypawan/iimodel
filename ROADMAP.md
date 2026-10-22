# II Product Roadmap

This roadmap governs the decentralized worker + local Jarvis product. Nothing is complete merely because it appears in a README or database; every phase has a physical or automated exit criterion.

## v0.3 — Trusted background worker foundation (implemented)

- [x] Ed25519 host identity and signed mutable requests.
- [x] Replay/clock-window checks.
- [x] Publisher-signed model manifests.
- [x] Exact manifest-backed host artifact claims.
- [x] Separate desired assignments from hosted truth.
- [x] Content-addressed artifact storage and HTTP resume.
- [x] Complete-GGUF local inference or strict HTTP 503.
- [x] Encrypted durable jobs with leases, retries, receipts, and cancellation.
- [x] Pull workers that operate behind NAT.
- [x] Explicit, encrypted, quarantined teaching intake with deletion.
- [x] Sandboxed Electron renderer/preload boundary.

Evidence: Node test suites exercise protocol tampering, migration, routing truth, encryption, job lifecycle, privacy rejection, physical file allocation/resume, and fail-closed inference.

Limit: no production model or physical Electron GUI/model run was available in the build sandbox.

## v0.4 — Production worker pilot

Build:

- select one exact legally redistributable worker GGUF,
- reproducible conversion/quantization record,
- clean-machine installer,
- remote readiness challenge jobs,
- host key rotation/recovery and multi-coordinator revocation propagation,
- queue/worker operational dashboard,
- tested backup/restore,
- signed update channel for the Electron client,
- Dodo webhooks and subscription entitlements.

Exit criterion: ten physical desktops complete 1,000 controlled jobs with published latency, failure, retry, memory, and power measurements; one host failure is recovered without data corruption.

## v0.5 — Offline phone reflex

Build:

- Android shell,
- signed phone artifact updater,
- offline wake word, VAD, STT, TTS,
- benchmark-selected small local model,
- deterministic local actions,
- local/private/background routing,
- charging, Wi-Fi, metered data, storage, and privacy settings.

Exit criterion: named target Android devices complete the offline command suite with measured first-action/first-audio latency and screen-off/battery tests. Swarm outage cannot break local commands.

## v0.6 — Peer-assisted weight distribution

Build:

- signed per-chunk map/Merkle root,
- missing-chunk deduplication,
- authenticated peer transfer,
- peer discovery,
- NAT traversal and relay fallback,
- upload limits and consent,
- malicious/corrupt peer penalties,
- controlled HTTPS fallback.

Exit criterion: two clients upgrade while fetching only missing chunks, reject a tampered peer chunk, resume interruption, and succeed behind representative Indian home/mobile NAT configurations.

## v0.7 — Distributed-runtime research

Build:

- exact 1B–3B checkpoint and tensor topology,
- one-device baseline,
- two-desktop LAN runtime-supported split,
- authenticated activation transport,
- cancellation/backpressure,
- failure recovery experiment,
- complete published measurements.

Exit criterion: a reproducible physical run executes one complete model across two devices and compares first-token latency, tokens/second, bytes/token, memory, and recovery with one-device inference.

This phase does not become the voice path.

## v0.8 — Reliable background swarm

Build:

- capability benchmarks,
- placement by model/hardware/region/load,
- replication targets from measured churn,
- job-level hedging and loser cancellation,
- worker reputation with decay,
- duplicate/challenge result validation,
- multi-coordinator durable storage/rate limits,
- optional user-controlled cloud fallback.

Exit criterion: sustained controlled jobs meet a published completion SLO through injected churn and coordinator restart.

## v0.9 — Learning flywheel

Build:

- authorized review console,
- stronger PII/secret scanning,
- deduplication/provenance,
- exact-base adapter training pipeline,
- Hindi/Marathi/Hinglish and domain benchmark suites,
- safety and privacy extraction gates,
- signed candidate adapter registry,
- staged rollout and rollback,
- contributor consent withdrawal through derived-data policy.

Exit criterion: one adapter improves a predeclared target with confidence, passes all non-regression/privacy gates, retains traceable consent, and is safely rolled back in rehearsal.

No automatic user-adapter merge is planned before that evidence.

## v1.0 — Public product gate

Required:

- Android local voice release,
- stable background worker service,
- production subscription entitlement system,
- host/result abuse controls,
- independent security review,
- DPDP/privacy/legal documentation,
- Genesis eligibility and reward terms,
- incident and support operations,
- measured product SLOs.

A 20-device/full-MoE-copy claim additionally requires an exact tensor topology and successful complete forward pass across physical hosts. It is not a prerequisite for delivering useful background jobs through complete-model workers.

## Decisions intentionally deferred

- Exact Qwen/DeepSeek/other checkpoints until license and target-device benchmarks are reviewed.
- Blockchain/token incentives until legal and Sybil concerns are solved.
- Phone WAN hosting until local voice is stable and battery/network evidence exists.
- Remote MoE execution until shared-backbone and per-layer expert protocol is proven.
- Fully decentralized control until central protocol, revocation, billing, and abuse response are production-safe.
