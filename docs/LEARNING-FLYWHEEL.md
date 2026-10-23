# II Learning Flywheel

## Current status

v0.4 implements the **review, provenance, orchestration metadata, release-gate, signing, rollout, rollback, and withdrawal control plane**. It does not train a model. No GPU, real dataset, adapter artifact, or benchmark/privacy evidence was supplied, so no production adapter is claimed.

The human console is served at `/review.html`; its API lives in `network/learning.js`.

## Non-negotiable rules

1. Raw conversations are never silently used for training.
2. Browser-vault/private-memory data is excluded unless a contributor explicitly creates and reviews a teaching record.
3. The contributor sees the exact prompt/correction and consent version.
4. An adapter is bound to one exact base manifest; family/tokenizer similarity is insufficient.
5. An adapter can memorize private text and is not anonymous.
6. No quarantined item, user adapter, or community adapter is automatically trained, merged, routed, or published.
7. Dataset sealing, training evidence, privacy/benchmark gates, human approval, signing, and rollout are separate transitions.
8. Every release can be stopped or rolled back.

## Intake and privacy scanning

`POST /teachings` requires the teaching token, exact active manifest, bounded prompt/correction, language/category, and `consent_version=2026-10-22`.

The deterministic v2 scan rejects findings including:

- email, Indian phone, Aadhaar-like, PAN, passport-like, IFSC, labelled bank-account patterns;
- Luhn-valid payment-card candidates;
- IPv4 addresses;
- private keys, AWS/GitHub/API tokens, JWTs, bearer tokens;
- database connection URLs and password/secret assignments;
- high-entropy token-like values.

This is a gate, not proof of anonymization. It can have false positives and false negatives. Accepted text is AES-256-GCM encrypted and remains `quarantined`. List/status responses exclude plaintext.

## Human review

An admin reviewer must explicitly fetch `/admin/teachings/:id/content`. Every plaintext view writes an audit event. Reviewers submit one of:

- `approved`;
- `rejected`;
- `needs_changes` (returns the item to quarantine).

The coordinator rescans content at review time. An item with findings cannot be approved. Reviewer notes are encrypted with record-specific authenticated context.

The console does not place admin tokens in persistent browser storage; they live in the tab session.

## Immutable dataset versions

`POST /admin/datasets` accepts only approved item IDs from one exact source manifest. Each item records:

```text
teaching ID
consent version
source manifest ID
human approval review IDs/reviewers/timestamps
hash of teaching ID + prompt + correction + consent version
```

A draft becomes immutable through `POST /admin/datasets/:id/seal`. Sealing commits a SHA-256 hash over the ordered item-hash set and source manifest. Training requests require a sealed dataset.

## External training orchestration

`POST /admin/training-runs` stores a bounded, secret-scanned configuration and environment record for an external trainer. It returns `execution: external_orchestrator_required`.

The coordinator intentionally does not shell out to a trainer, claim a GPU, or invent metrics. An external operator/runner reports state through `/admin/training-runs/:id/report`.

A run may reach `awaiting_approval` only with:

- `metrics.benchmark_passed === true`;
- `privacy_report.passed === true`;
- exact 64-hex artifact SHA-256;
- positive exact byte size;
- HTTPS artifact URL.

These booleans are operator evidence inputs. Production operation still needs signed/raw reports, independent review, and artifact verification by release tooling.

## Human approval and signed publication

A separate human approves an `awaiting_approval` run. Publication then requires an Ed25519 key in `II_ADAPTER_SIGNING_PRIVATE_KEY` and emits a canonical `ii.adapter-release.v1` document binding:

```text
adapter release ID
training run and immutable dataset IDs/hash
base manifest ID
artifact SHA-256, size, and HTTPS URL
benchmark metadata
privacy-gate metadata
human approver
publication timestamp
publisher public key and signature
```

The private key must come from deployment secret management and remain out of Git. The software test uses an ephemeral key; that is not production key ceremony.

## Rollout and rollback

A signed release begins `staged` at 0%. An admin can set a bounded 0–100 rollout percentage. Nonzero rollout marks it active. Rollback sets the candidate to 0 and can restore a named non-withdrawn release.

A production rollout still needs outcome monitoring, target cohorts, automatic stop thresholds, compatibility checks, and rehearsal.

## Contributor withdrawal

The receipt-authenticated contributor can withdraw at any time. The system:

- tombstones encrypted prompt/correction content;
- rotates away the receipt hash;
- records affected datasets and releases;
- removes affected staged/active adapters from rollout;
- records the request and limitation.

If an item entered a sealed dataset or published weights, the coordinator states that already-trained weights cannot be selectively untrained by this control plane. It prevents further rollout but does not pretend cryptographic deletion can reverse gradient updates. Legal/product policy must decide retraining and derived-artifact removal.

## Real benchmark gate still required

Before a real adapter may ship, predeclare thresholds and produce evidence for:

### Capability

- narrow target-domain improvement with confidence intervals;
- general reasoning, coding, and instruction non-regression where relevant;
- Hindi, Marathi, Hinglish, and English evaluation;
- one exact frozen base/checkpoint comparison.

### Safety

- policy and dangerous-action tests;
- jailbreak and false-authority regressions;
- hallucination/format checks;
- human language/domain review.

### Privacy

- canary extraction;
- membership-inference risk;
- direct identifier generation;
- nearest-neighbour review against contributed text;
- poisoning and duplicate analysis.

### Operations

- load compatibility and exact base hash;
- memory and tokens/second regression;
- deterministic artifact hash;
- staged deployment and rollback rehearsal.

## Federated training remains deferred

Federated LoRA additionally needs signed training code, exact-base capable devices, update clipping/bounds, secure aggregation, differential-privacy accounting, Byzantine/poisoning defence, cohort sizing, dropout recovery, and battery/charging consent. Phones that do not hold the worker base cannot train its adapter, and a phone-model adapter cannot be merged directly into a larger worker model.

## Production exit criterion

One real signed adapter must improve a predeclared target with reviewed confidence, pass all safety/non-regression/privacy gates, preserve traceable consent/provenance, load against the exact signed base, canary safely, and roll back in rehearsal. The v0.4 software makes these states explicit; it does not fabricate their evidence.
