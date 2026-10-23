# II Product Roadmap

Nothing is complete merely because a route or table exists. **Software-complete**, **locally tested**, and **externally proven** are separate states.

## v0.4 — Reliability/product reference (implemented in software)

Automated/local evidence now covers:

- [x] per-job llama context/session isolation;
- [x] stronger contribution secret/PII scanning;
- [x] user, identity, plan, checkout, subscription, entitlement, usage, audit, and fraud models;
- [x] capability/load/declared-region scheduling;
- [x] delayed hedging capped at two attempts and loser cancellation state;
- [x] deterministic challenges, duplicate comparison, score decay, and quarantine;
- [x] manifest-specific scores and artifact/region replica targets;
- [x] worker churn, conflict, corruption, interruption, and coordinator restart chaos tests;
- [x] Android/iOS/desktop browser PWA shell with encrypted local conversations;
- [x] foreground push-to-talk and strict local-model adapter boundary;
- [x] background job submission/status/notification UI;
- [x] WebGPU/storage/microphone/memory capability checks;
- [x] signed three-process local peer discovery/content/relay simulation;
- [x] range resume, limits, corruption rejection, reputation log, and HTTP fallback;
- [x] real two-process binary activation transport, measurements, and failure timing;
- [x] authorized learning review console, provenance, immutable datasets, external-run metadata, gates, approval, signed publication, rollout/rollback, and withdrawal;
- [x] passwordless identity boundary, correlated Dodo checkouts, Standard Webhooks verification, idempotent event storage, lifecycle state, quotas, Genesis qualification, audit, and fraud flags.

Local evidence does **not** close the gates below.

## v0.4 pilot gates — external evidence required

- [ ] Select one exact legally redistributable Qwen-family worker GGUF from predeclared benchmarks.
- [ ] Record checkpoint/tokenizer hashes, conversion/quantization procedure, license review, and model card.
- [ ] Build and smoke-test signed Windows/macOS installers on clean machines.
- [ ] Execute 1,000 controlled jobs across at least ten physical desktops; publish latency, completion, challenge, hedge, memory, power, and churn results.
- [ ] Restore a backed-up production-like coordinator and rotate/recover a host key.
- [ ] Validate Dodo test-mode checkout, signed webhook, renewal, cancellation, refund, and dispute deliveries end-to-end.
- [ ] Complete tax, privacy, incident, support, Genesis eligibility, and reward-term review.

Exit criterion: the pilot SLO and business lifecycle pass with real evidence; local tests alone are insufficient.

## v0.5 — Browser product validation and native voice decision

The PWA is implemented, but device claims require a matrix:

- [ ] install/update/offline-shell test on named Android Chrome, iOS Safari, Windows, and macOS versions;
- [ ] microphone, speech interface, notification, storage eviction, WebGPU, memory pressure, and app-restoration results;
- [ ] benchmark-selected foreground local model adapter on supported devices;
- [ ] privacy/local/background policy usability tests;
- [ ] determine whether product requirements need a native Android foreground service and offline STT/TTS.

Exit criterion: published device results. Browser mobile must not be represented as reliable for always-on wake word, screen-off capture, or screen-off inference.

## v0.6 — Operated peer distribution

The authenticated local reference is implemented. Production requires:

- [ ] deployed bootstrap identity/key rotation;
- [ ] consent and upload controls in the desktop product;
- [ ] operated relay capacity, abuse/rate policy, and observability;
- [ ] representative Indian home broadband, CGNAT, mobile, IPv4, and IPv6 tests;
- [ ] optional standard NAT traversal transport after dependency/security review;
- [ ] signed multi-chunk/Merkle manifests for partial artifact upgrades.

Exit criterion: two real clients resume and verify only required bytes through direct, NAT, relay, and HTTPS paths while rejecting corruption.

## v0.7 — Physical two-node Qwen proof

The deterministic two-process transport reference is implemented. It is not model evidence.

- [ ] freeze an exact licensed 1B–3B checkpoint and tokenizer;
- [ ] publish exact topology, dtype/quantization, activation shape, and KV ownership;
- [ ] run one-device baseline;
- [ ] run a runtime-supported split on two physical LAN machines;
- [ ] record first-token latency, tokens/second, activation bytes/token, compute/transport time, RSS/VRAM, output comparison, cancellation, and stage failure;
- [ ] compare capability and throughput with complete-model replication.

Exit criterion: a reproducible complete forward pass across two machines. This never becomes the instant voice path by default.

## v0.8 — Production reliability

The single-coordinator algorithms are implemented locally. Production requires:

- [ ] measured thresholds for score deltas, decay, quarantine, hedging, and replica targets;
- [ ] challenge corpus security and rotation;
- [ ] multi-coordinator durable attempt arbitration and cancellation delivery;
- [ ] backed-up, horizontally safe data store;
- [ ] queue/worker/reputation operational dashboard and incident runbooks;
- [ ] optional user-controlled cloud fallback.

Exit criterion: sustained controlled jobs meet published completion and latency SLOs through process, host, network, and region failure injection.

## v0.9 — Evidence-producing learning run

The review/orchestration/publication control plane is implemented. External work remains:

- [ ] legally reviewed, consented dataset content;
- [ ] deduplication and language/domain review at operating scale;
- [ ] real GPU adapter training against the exact base hash;
- [ ] predeclared Hindi/Marathi/Hinglish/English capability and safety suites;
- [ ] memorization, extraction, and membership-inference evaluation;
- [ ] human release approval with protected signing key;
- [ ] canary monitoring and rollback rehearsal;
- [ ] derived-data handling decision for contributor withdrawal.

Exit criterion: one real adapter improves a predeclared target with confidence, passes every non-regression/privacy gate, retains traceable consent, and is safely rolled back in rehearsal.

## v1.0 — Public product gate

Required:

- verified PWA/native client matrix;
- exact signed production checkpoint releases;
- stable background worker service and abuse controls;
- live subscription entitlement/reconciliation operation;
- independent security/privacy review;
- DPDP and jurisdiction-specific legal documentation;
- Genesis eligibility/reward terms;
- incident, support, backup, and model-removal procedures;
- measured product SLOs.

A “20 executable MoE copies” claim additionally requires exact tensor topology and physical complete-forward-pass evidence. Storage arithmetic or expert-map coverage is never sufficient.
