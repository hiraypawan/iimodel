# II Network Operations

## Deployment boundary

Use one v0.4 coordinator behind TLS and an authenticating/rate-limiting reverse proxy for a pilot. Keep SQLite WAL files on persistent encrypted storage and run consistent backups. Pull workers need outbound HTTPS only; never expose their loopback llama port `7777`.

The local peer and activation processes are references. Do not expose them publicly without a separate threat review, TLS, operated bootstrap/relay policy, monitoring, and capacity controls.

## Configuration and secrets

Required core:

```text
II_ADMIN_API_TOKEN
ADMIN_EXPORT_TOKEN
II_JOB_API_TOKEN
II_TEACHING_API_TOKEN
II_DATA_ENCRYPTION_KEY          exactly 32 decoded bytes
II_AUDIT_HASH_KEY               high-entropy HMAC salt
II_RELEASE_PUBLIC_KEY           public but integrity-sensitive
```

Identity:

```text
II_AUTH_DELIVERY_WEBHOOK_URL    HTTPS transactional-email adapter
II_AUTH_DELIVERY_WEBHOOK_TOKEN
II_AUTH_TEST_MODE=false         never enable in production
```

Dodo:

```text
DODO_PAYMENTS_ENVIRONMENT
DODO_PAYMENTS_API_KEY
DODO_PAYMENTS_WEBHOOK_KEY       Standard Webhooks whsec value
DODO_MONTHLY_PRODUCT_ID
DODO_YEARLY_PRODUCT_ID
```

Learning publication:

```text
II_ADAPTER_SIGNING_PRIVATE_KEY  protected Ed25519 key; separate from model key
```

Store secrets in deployment secret management, never committed `.env` files. Keep release/adapter signing keys offline where possible.

## Startup checks

- DB directory is persistent/writable and SQLite foreign keys/WAL are active;
- data key decodes correctly and restore data uses the matching historical key;
- public release key matches its out-of-band fingerprint;
- admin/job/teaching/delivery/webhook credentials are distinct and high entropy;
- `II_AUTH_TEST_MODE` is false;
- CORS origins and trusted proxy exactly match deployment topology;
- raw `/webhooks/dodo` body reaches Express unchanged;
- TLS, system clock, disk limits, backup, and restore alerting are healthy;
- Dodo product IDs use correct country pricing and smallest currency units;
- a safe test identity, checkout, event, and entitlement lifecycle has passed.

## Model release process

1. Freeze exact checkpoint/tokenizer and review license/model card.
2. Reproduce conversion/quantization.
3. Hash exact bytes and record size.
4. Run quality, safety, runtime, memory, power, and language benchmarks.
5. Prepare unsigned manifest and evidence index.
6. Sign in restricted/offline process.
7. Independently verify on a clean machine.
8. Publish manifest/mirrors.
9. Canary named workers.
10. Review load/challenge/result telemetry before expansion.

Never edit signed manifests; publish a new version/ID.

## Reliability operations

Monitor by exact manifest:

- ready capacity and declared region/load;
- replica and region deficits;
- queued/leased age and attempts;
- hedge creation/cancellation/late-result rate;
- completion/failure/expiry p50/p95;
- challenge pass/fail and conflict rate;
- decayed score and quarantine count/reason;
- host revocation and model deactivation;
- DB lock/transaction/backup health.

Score parameters are pilot defaults, not scientifically calibrated trust. Investigate conflicts; do not blindly release quarantine or permanently punish both workers without evidence.

## Identity/billing operations

- protect the passwordless delivery adapter; never log login tokens;
- reconcile provider customers/subscriptions/payments with local records;
- alert on webhook signature failures, duplicate-ID payload mismatch, failed processing, correlation failures, refunds, disputes, and chargebacks;
- resolve fraud flags with an audit trail;
- treat redirects as UI only—entitlements come from verified provider events;
- test renewal, pause/hold, cancellation, expiry, refund, and dispute flows in Dodo test mode before live;
- preserve encrypted webhook records per retention policy;
- document support/account recovery and token revocation.

Pre-created payment links need equivalent metadata/correlation configuration. Otherwise do not auto-entitle their redirects.

## Learning operations

- use `/review.html` only over protected HTTPS/admin access;
- reviewer IDs must map to real operator identities at the edge/IAM layer;
- minimize plaintext review exposure and never copy content into logs/tickets;
- seal only reviewed, same-base datasets;
- verify external benchmark/privacy reports and artifact bytes independently;
- require separate human approval and protected signing;
- canary rollout with stop thresholds and rehearsed rollback;
- process contributor withdrawal and decide when retraining/derived-artifact deletion is required.

The coordinator never proves that external GPU work happened merely because metadata says it did.

## Incident actions

### Compromised model/adapter release

Deactivate/rollback, set rollout to zero, remove mirror access, preserve evidence, inspect affected jobs/users, publish an advisory/replacement, and rotate signer only if signer compromise is plausible.

### Compromised host

`POST /admin/hosts/:deviceId/revoke` persists revocation, removes claims/endpoint, cancels active attempts, and requeues/fails affected jobs according to bounds. Block at the edge and investigate challenges/conflicts.

### Data-key compromise

Stop intake, restrict DB access, preserve evidence, run an explicit decrypt/re-encrypt migration to a new key, and follow breach procedure. Replacing the env key without migration makes old ciphertext unreadable.

### Coordinator crash

The automated suite proves a local SQLite WAL `SIGKILL`/restart case with an active attempt. Production still needs process supervision, consistent backups, restore rehearsal, disk corruption handling, and recovery objectives.

### Payment incident

Disable checkout if needed, continue signature-verified event capture, reconcile directly with Dodo, suspend only with reviewed state, preserve fraud/audit data, notify affected users under policy, and never infer entitlement from browser success parameters.

## Backups

Back up consistently:

- SQLite DB/WAL through SQLite-safe backup procedure;
- model/adapter manifests, signatures, and public-key history;
- configuration inventory (without plaintext secrets where possible);
- deployment/audit/reconciliation records;
- external evidence reports and artifact hashes.

Restore into an isolated environment, verify encrypted rows with corresponding historical key, disable outbound worker/payment delivery, and run integrity/lifecycle checks.

## Scale transition

Before multiple coordinators:

- durable distributed rate limits;
- transactional shared DB and migrations;
- globally atomic attempt/hedge winner arbitration;
- durable cancellation/event queue;
- shared revocation and score state;
- idempotent out-of-order webhook reducer/reconciliation;
- centralized audit with content minimization;
- tested failover and rollback.

A DHT does not replace durable jobs, release trust, billing, identity, or abuse response.
