# Distributed Inference Runtime Plan

Status: not implemented. v0.3 runs complete compatible GGUF worker replicas and treats distributed tensors as a separate measured research track.

## Why metadata sharding is insufficient

A database mapping `expert_id → host` does not execute a model. A transformer forward pass also needs exact shared tensors, layer order, activation shapes, router decisions, KV-cache ownership, dtype/quantization compatibility, sampling state, and cancellation/failure semantics.

Many MoE architectures have routed experts at multiple layers. “256 experts” may mean experts per MoE layer rather than 256 standalone global files. Dividing total bytes by 256 is not an executable shard definition.

## Required tensor topology

A future signed topology must identify:

```text
checkpoint and tokenizer hashes
layer count and ordering
shared tensors
attention tensors
router tensors
expert tensors keyed by layer and expert index
input/output activation dtype and shape
quantization parameters
KV-cache ownership
runtime ABI
```

## Recommended experiment order

### A. Baseline

Run one exact 1B–3B model on one desktop. Record first-token latency, tokens/second, RSS/VRAM, power, and output hash in deterministic mode.

### B. Two desktops on one LAN

Use a runtime that genuinely supports layer/RPC splitting. Do not manually cut GGUF bytes. Record activation bytes, serialization cost, compute time per stage, and end-to-end metrics.

### C. Failure behavior

Disconnect stage two during prefill and generation. The request must time out/cancel cleanly, free cache, and either restart on a complete replica or return a truthful failure.

### D. Three LAN stages

Advance only if the two-node result provides a useful capability/throughput tradeoff.

### E. Controlled WAN

Test fixed stable hosts and publish p50/p95/p99. Random phones are not the first WAN target.

## Protocol requirements

- request/session IDs,
- model and topology IDs,
- stage lease and expiry,
- tensor sequence numbers,
- shape/dtype validation,
- bounded activation frames,
- authenticated encrypted transport,
- backpressure,
- prefill/generation distinction,
- KV-cache lifecycle,
- cancellation propagation,
- retry boundary,
- metrics without prompt logging.

## Parallelism choices

### Complete-model replication

Implemented direction for v0.3 jobs. It increases throughput and reliability and avoids per-token WAN hops. This is the default production path.

### Pipeline parallelism

Research path. Every autoregressive token traverses sequential stages. More hosts improve aggregate throughput but not the dependency chain for one sequence.

### Tensor parallelism

Not suitable over ordinary internet links; it expects very high bandwidth and low latency.

### Remote MoE experts

Potentially useful only with a custom runtime and local/shared backbone. Expert calls may occur at many transformer layers; it is not simply two network requests per answer.

### Hedging

Initially hedge complete background jobs, select one valid attempt, and cancel losers. Hedging consumes multiple workers and should be policy-controlled. Do not splice tokens from independently sampled pipelines.

## Churn

A distributed stage plan must be selected from compatible live hosts with replication. If a stage disappears mid-token, another host usually cannot resume without the exact KV/cache state. Practical recovery may require restarting generation on a complete replica.

## Privacy

Intermediate activations may leak prompt information. Encryption protects transit, not the peer doing computation. Do not market activations as anonymous.

## Exit criterion

II may claim distributed inference only after a reproducible physical multi-node test executes one exact model, publishes topology and metrics, survives a defined failure test, and is compared with a one-device baseline. Until then, the production worker network means complete-model background replicas.
