# Distributed Inference Runtime

## Current status

v0.4 includes a **real local activation-transport reference** in `distributed/`:

- two independent Node worker processes;
- binary little-endian `Float32` activation frames over HTTP;
- contiguous layer partitions;
- HMAC authentication, timestamp window, nonce replay rejection;
- bounded frame/layer/width validation;
- input/output SHA-256 checks;
- per-stage bytes, total time, reported compute time, and measured transport overhead;
- p50/p95 benchmark harness;
- explicit stage/elapsed failure when a worker is killed.

The computation is `deterministic-nonlinear-v1`, not a transformer. Every result states `production_model: false`. This proves that bytes really cross process/network sockets and that failure is measurable; it does **not** prove Qwen, GGUF splitting, a physical LAN, or useful distributed-model performance.

Run the local reference:

```bash
export II_ACTIVATION_SECRET='<at least 24 random bytes>'
HOST=0.0.0.0 PORT=7801 node distributed/activation-worker.js
HOST=0.0.0.0 PORT=7802 node distributed/activation-worker.js
II_ACTIVATION_NODES=http://127.0.0.1:7801,http://127.0.0.1:7802 \
  node distributed/benchmark.js
```

## Why expert metadata is not inference

A SQLite mapping `expert_id → host` does not execute a model. A transformer forward pass also requires exact shared tensors, layer order, router decisions, activation shapes, quantization/dtype agreement, tokenizer and sampling state, KV-cache ownership, backpressure, cancellation, and failure semantics.

Many MoE checkpoints use experts at multiple layers. “256 experts” may mean experts per MoE layer—not 256 globally independent files. Dividing bytes by 256 is not an executable topology.

The coordinator therefore routes only explicit expert IDs from manifest-backed SQLite claims. It no longer hashes a prompt into five pretend experts. Complete compatible GGUF replicas remain the production background-job path.

## Physical two-node Qwen gate

A valid physical proof requires all of the following:

```text
exact checkpoint and tokenizer names + hashes
verified license/model-card/commercial redistribution terms
reproducible conversion and quantization record
exact layer/tensor partition and runtime ABI
activation dtype, shape, framing, and quantization
shared tensor and KV-cache ownership
one-device baseline on named hardware
second machine on a described physical LAN
full prompt-to-generated-token execution
failure/cancellation/cache cleanup result
published raw and summarized measurements
```

A Qwen-family candidate is preferred, but no exact checkpoint is selected in this repository because weights, license evidence, target hardware, and measurements were not supplied.

## Required experiment order

### A. One-device baseline

Run the exact 1B–3B candidate on one desktop. Record:

- first-token p50/p95;
- decode tokens/second;
- peak RSS/VRAM;
- prompt and output token counts;
- power where available;
- deterministic-mode output hash and capability score.

### B. Two physical LAN nodes

Use a runtime that genuinely supports layer/RPC splitting. Do not cut GGUF bytes manually. Record:

- activation bytes for prefill and per generated token;
- serialization/deserialization time;
- compute time per stage;
- end-to-end first-token and decode latency;
- throughput with one and concurrent requests;
- memory and network utilization on both machines.

### C. Failure behavior

Disconnect node two during prefill and generation. The request must time out/cancel within a declared bound, free stage state, and either restart from a complete replica or return a truthful failure. It must not splice unrelated independently sampled token streams.

### D. Larger topology/WAN

Advance to three LAN stages only if two nodes offer a useful tradeoff. Controlled WAN tests come later and must publish p50/p95/p99. Random phones are not the first distributed runtime target.

## Protocol requirements beyond the local reference

- topology and model IDs bound into every request;
- session/stage lease and expiry;
- tensor sequence numbers;
- shape/dtype/quantization validation;
- encrypted transport in addition to authentication;
- backpressure and bounded queueing;
- prefill/generation distinction;
- KV-cache allocation/ownership/lifetime;
- cancellation propagation;
- restart boundary and complete-replica fallback;
- metrics without prompt or activation logging.

## Parallelism choices

### Complete-model replication

Implemented for background jobs. It improves aggregate throughput/reliability and avoids per-token WAN dependencies.

### Pipeline parallelism

Physical research gate. Every autoregressive token crosses sequential stages, so network and slowest-stage latency matter.

### Tensor parallelism

Generally unsuitable for ordinary internet links; it expects very high bandwidth and low latency.

### Remote MoE experts

Requires a custom runtime and exact shared backbone. Expert calls can occur at many transformer layers and are not “two network requests per answer.”

### Job hedging

Implemented separately for complete background jobs. At most two workers run a delayed duplicate; the first accepted result cancels the other. Duplicate verification compares complete normalized result hashes. Tokens from separate attempts are never combined.

## Privacy

Activations can leak information about prompts. Encryption protects transport, not a stage that performs computation. Do not market intermediate tensors as anonymous.

## Claim boundary

II may claim **local two-process activation transport** now. It may claim **physical distributed model inference** only after the physical gate above runs one exact licensed checkpoint across two machines and publishes comparable evidence.
