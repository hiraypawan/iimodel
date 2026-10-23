# Network and Product Benchmark Contract

No latency, intelligence, capacity, battery, payment, privacy, or cost claim is shipped without reproducible evidence under this contract. A local automated test establishes software behavior—not physical or production performance.

## Report identity

Every report records:

- Git commit and uncommitted patch state;
- signed manifest ID and artifact/tokenizer hashes;
- runtime, dependency, OS, browser, and app versions;
- exact CPU/GPU/RAM/storage/device hardware;
- quantization/context/runtime settings;
- network topology, physical/virtual status, RTT/bandwidth/loss;
- date, declared/actual test region, power mode, warm/cold state;
- predeclared sample count and exclusions;
- p50/p95/p99 where relevant;
- raw result/evidence artifact hash.

## Worker/model baseline

For every exact Qwen-family or other candidate:

- license/model-card review reference;
- load success/time and artifact verification;
- peak RSS/VRAM and disk bytes;
- time to first token;
- prefill and decode tokens/second;
- completion/correctness benchmark by language/domain;
- 30-minute thermal/power behavior;
- deterministic/challenge behavior;
- isolated-context regression and memory-leak observation.

A family name or parameter count is not a benchmark.

## Scheduler reliability

Inject at minimum:

1. incompatible/no-artifact worker;
2. worker disappears before and during inference;
3. lease expiry and retry exhaustion;
4. model deactivation/revocation;
5. delayed hedge before/after winner;
6. cancellation ignored/late loser completion;
7. matching and conflicting duplicate outputs;
8. challenge pass/failure/quarantine/release;
9. region unavailable and reported-load saturation;
10. coordinator process kill/restart with queued and active work;
11. DB backup/restore and disk pressure.

Report completion rate, recovery time, duplicate compute, false quarantine, oldest queue age, and p50/p95/p99 by manifest/region/hardware.

v0.4 automation covers scheduling branches, bounded hedging, conflict quarantine, challenge score updates, lease expiry/churn, and a local SQLite `SIGKILL`/restart. It does not establish a production SLO.

## Peer distribution

Local automated evidence covers:

- signed announcement/directory and replay rejection;
- authenticated direct/relay ranges;
- interrupted relayed chunk and partial resume;
- range/concurrency/byte limits;
- final SHA-256 and corruption quarantine;
- controlled HTTP fallback;
- three independent processes.

External report additionally needs bootstrap/relay discovery time, representative NAT success, relay percentage/capacity/cost, upload consent/budget, disk-full behavior, malicious slow peers, IPv4/IPv6, CGNAT/mobile networks, and HTTP mirror behavior.

## Browser/PWA and voice

For each named Android/iOS/desktop browser/OS:

- installation/update/uninstall;
- offline shell and storage eviction;
- vault setup/unlock/wrong-passphrase/data-loss;
- microphone and Speech Recognition behavior;
- foreground/background/locked notification behavior;
- WebGPU/local-adapter capability, memory, thermal, and battery;
- local vs remote first action/first audio;
- app restoration and permission revocation.

Never average local reflex and remote background completion into one “Jarvis latency.” Browser tests cannot establish always-on screen-off behavior if the platform suspends them.

## Distributed activations/models

The local `deterministic-nonlinear-v1` report records real socket bytes, per-stage time/hash/PID, p50/p95, and bounded killed-node failure. Label it only as activation-transport evidence.

A model report must compare the exact checkpoint on one participating machine versus two physical LAN machines and include full prompt-to-token execution, first token, decode throughput, bytes/token, activation serialization, compute/transport stage timing, memory, output/capability comparison, cancellation, and failure recovery.

## Learning

A candidate adapter report includes:

- immutable dataset/provenance/consent hash;
- exact base and training code/environment;
- target improvement and every frozen non-regression set;
- language slices and confidence intervals;
- safety/jailbreak/dangerous-action results;
- canary extraction, direct identifiers, nearest-neighbour and membership-inference analysis;
- adapter/base load compatibility, latency/memory change;
- human data/release approvals;
- canary outcome and rollback rehearsal.

Database booleans are gate inputs, not proof; attach independently verified raw reports.

## Identity and payments

Production report covers:

- passwordless delivery/account recovery/rate abuse;
- token expiration/revocation and quota race behavior;
- Dodo sandbox/live checkout correlation;
- valid/invalid/stale/replayed webhook;
- out-of-order active/renew/cancel/expire/refund/dispute events;
- reconciliation and duplicate-ID/body-mismatch behavior;
- entitlement/usage consistency;
- fraud/support workflows;
- tax/accounting/legal sign-off.

Local fake-provider tests are contract tests only.

## Current automated evidence

The repository currently tests:

- Ed25519 identities, canonical requests, replay/tamper rejection, trusted manifests;
- forward migration and encrypted context-bound records;
- per-job llama context/session creation and disposal;
- capability/load/region schedule, two-attempt hedge, cancellation, duplicate conflict, challenge, decay/quarantine data, replica deficits;
- coordinator crash/restart and lease churn;
- stronger contribution scanning, audited review, datasets, external-run gates, signed adapters, rollout and withdrawal;
- identity token hashing, checkout correlation, Standard Webhooks idempotency, entitlement and chargeback/fraud state;
- PWA syntax/install/offline/encryption/local-adapter boundaries;
- three-process peer resume/relay/fallback/corruption/limits;
- two-process activation measurements and killed-node failure;
- real resumable local artifact allocation and strict inference HTTP 503.

It does **not** establish production model quality, physical device/LAN/NAT performance, real GPU training/privacy evidence, live Dodo operation, or binding legal/financial completion.
