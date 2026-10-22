# II Learning Flywheel

Status: safe intake foundation implemented; training, benchmark approval, merging, and release automation are not implemented.

## Product goal

More users should improve II through **reviewed teaching**, not merely create more identical model copies. Inference contributes availability and throughput. A learning flywheel requires explicit data contribution, compatible training, evaluation, safety review, and signed release management.

## Non-negotiable rules

1. Raw chats are never silently used for training.
2. Vault/private-memory data is excluded by default.
3. The contributor sees the exact prompt/correction before consent.
4. A LoRA is model-version-specific; same-family or same-tokenizer models are not automatically compatible.
5. An adapter can memorize private text and is not treated as anonymous.
6. No user adapter is automatically merged or routed in production.
7. Every accepted release can be rolled back.

## Implemented v0.3 intake

`POST /teachings` requires:

- `II_TEACHING_API_TOKEN`,
- exact active `manifest_id`,
- prompt and correction,
- language and category metadata,
- `consent=true`,
- `consent_version=2026-10-22`.

Before storage, the coordinator rejects common direct identifiers and secrets, including email, Indian phone, Aadhaar-like numbers, private keys, API-key patterns, and bearer tokens. This is a conservative first filter, not a complete anonymizer.

Accepted text is AES-256-GCM encrypted, marked `quarantined`, and returned with a one-time receipt token. Status responses exclude content. `DELETE /teachings/:id` permanently removes the row for consent withdrawal.

No v0.3 process reads quarantined content for training.

## Required review pipeline

```text
Local correction
  → user review and explicit consent
  → automated PII/secret scan
  → encrypted quarantine
  → authorized human/data review
  → deduplication and provenance record
  → train adapter for one exact base hash
  → benchmark + safety + privacy gates
  → release review
  → signed candidate
  → staged rollout
  → monitor and rollback
```

## Adapter metadata

Every candidate adapter must record:

```text
adapter_id
base_manifest_id
base_artifact_sha256
tokenizer_sha256
training_code_revision
rank / alpha / target modules
dataset provenance IDs
consent policy version
training metrics
benchmark report hash
privacy report hash
license
publisher signature
```

A “50 MB adapter” is a rough product example, not a protocol constant. Size depends on model dimensions, rank, targeted modules, and precision.

## Benchmark gate

An adapter cannot advance unless it passes all configured gates:

### Capability

- target-domain improvement,
- general reasoning non-regression,
- coding non-regression where applicable,
- Hindi, Marathi, Hinglish, and English sets,
- format/instruction following.

### Safety

- disallowed-content policy tests,
- jailbreak regression set,
- dangerous-action refusal tests,
- false-authority and hallucination checks.

### Privacy

- canary extraction,
- membership-inference risk checks,
- direct identifier generation,
- nearest-neighbor review against contributed examples.

### Operations

- load compatibility,
- memory increase,
- tokens/second regression,
- deterministic artifact hashes,
- rollback test.

Scores need confidence intervals and reviewed thresholds. Passing a benchmark does not prove broad safety.

## Merge policy

Initially, train and review adapters centrally on controlled infrastructure. Consider TIES/model-soup/MoE adapter merging only when every candidate uses the exact same base tensors and target-module shapes.

Recommended first release process:

1. Select one narrow category.
2. Manually review all accepted examples.
3. Train one adapter.
4. Compare against frozen base.
5. Run full regression/privacy suite.
6. Conduct human review in relevant languages.
7. Publish as an optional signed candidate.
8. Canary to opted-in users.
9. Merge into a new base only after evidence.

## Federated training later

On-device/federated LoRA requires substantially more than uploading adapters:

- devices capable of training the exact base,
- clipping and update bounds,
- secure aggregation,
- differential-privacy budget,
- Byzantine/poisoning defenses,
- minimum cohort size,
- drop-out recovery,
- signed training code,
- battery/charging consent.

Phones that do not hold the large base cannot train its LoRA. A local phone-model adapter also cannot be merged directly into a larger worker model.

## Incentives

Start with attribution badges and changelog credit. Financial rewards require Sybil resistance, tax and labor analysis, contributor terms, and poisoning defenses. Never reward raw submission volume.

## Exit criterion

The learning flywheel becomes production-ready only when one signed candidate demonstrates a reviewed target improvement, no unacceptable regression, no detected privacy extraction, traceable consent/provenance, and a tested rollback.
