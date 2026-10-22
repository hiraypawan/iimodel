# Network and Product Benchmark Contract

No latency, intelligence, capacity, battery, or cost claim is considered shipped without a reproducible report under this contract.

## Report identity

Every report records:

- Git commit,
- signed manifest ID and artifact hashes,
- runtime and OS versions,
- exact hardware,
- quantization/context length,
- network topology and measured RTT/bandwidth,
- date and region,
- warm/cold state,
- sample count,
- p50/p95/p99 where relevant,
- raw result artifact hash.

## Worker baseline

Measure:

- load success and load time,
- peak RSS/VRAM,
- time to first token,
- prefill tokens/second,
- generation tokens/second,
- completion rate,
- 30-minute thermal behavior,
- job retry/failure rate.

## Scheduler reliability

Test at minimum:

1. worker disappears before lease,
2. worker disappears during inference,
3. lease expires,
4. model is deactivated during queueing,
5. corrupted result request,
6. duplicate completion,
7. coordinator restart with queued work,
8. incompatible model worker.

Report successful completion rate and recovery time. v0.3 implements durable queueing, leases, expiry/retry, manifest compatibility, and duplicate-state rejection; physical churn tests remain required.

## Distribution

For complete artifact downloads:

- full cold download,
- interrupted resume,
- mirror without Range support,
- hash mismatch,
- invalid format,
- disk-full behavior,
- unchanged content-addressed reuse.

Future peer transfer additionally reports peer discovery time, NAT success, relay rate, upload cost, and corrupted-peer handling.

## Phone voice

Use the metrics and target-device matrix in `PHONE-VOICE.md`. Separate local reflex results from remote deep-job completion. Never average them into one “Jarvis latency” number.

## Distributed tensors

Compare every multi-node run against the same model on the best single participating device. Include bytes per token and pipeline-stage timing; total nominal FLOPS is not a user-visible speed result.

## Learning

A candidate release report includes:

- frozen baseline,
- target improvement,
- all non-regression sets,
- language slices,
- confidence intervals,
- safety and privacy extraction results,
- adapter/base compatibility,
- latency and memory change,
- human review sign-off.

## Current automated evidence

The repository tests currently establish protocol and failure-boundary behavior, including:

- deterministic placement math,
- host signature tamper rejection,
- trusted manifest verification,
- legacy declaration migration without false capacity,
- encrypted durable jobs and receipt access,
- compatible worker lease/completion,
- teaching PII/secret rejection and quarantine,
- resumed physical 10 MiB artifact storage,
- strict inference HTTP 503 without a model,
- Dodo request/provisioning contracts,
- landing endpoint/pricing wiring.

They do not establish target-model quality, Android latency, WAN inference performance, live Dodo operation, or 20-device model execution.
