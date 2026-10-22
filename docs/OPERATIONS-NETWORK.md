# II Network Operations

## Pilot deployment

Use one coordinator behind TLS and an authenticating/rate-limiting reverse proxy. Keep SQLite on persistent encrypted storage with backups. Pull workers need outbound HTTPS only; do not expose their loopback port 7777.

Required coordinator secrets:

```text
II_ADMIN_API_TOKEN
ADMIN_EXPORT_TOKEN
II_JOB_API_TOKEN
II_TEACHING_API_TOKEN
II_DATA_ENCRYPTION_KEY
II_RELEASE_PUBLIC_KEY (public, but integrity-sensitive)
DODO_PAYMENTS_API_KEY when checkout is enabled
```

Store secrets in the deployment secret manager, not `.env` committed to Git.

## Startup checks

- database path is persistent and writable,
- encryption key decodes to exactly 32 bytes,
- release public key matches the out-of-band value,
- admin/job/teaching tokens are distinct and high entropy,
- CORS origin is explicit,
- proxy trust matches the exact proxy topology,
- TLS terminates only at trusted infrastructure,
- system clock is synchronized,
- backup and restore have been tested.

## Release process

1. Review model license and provenance.
2. Reproduce conversion/quantization.
3. Hash exact bytes.
4. Run quality/safety/runtime benchmarks.
5. Prepare unsigned manifest.
6. Sign with offline release key.
7. Independently verify on a clean machine.
8. Publish manifest and artifact mirrors.
9. Canary selected workers.
10. Activate broader release only after telemetry review.

Never edit a signed manifest. Publish a new version and manifest ID.

## Incident actions

### Compromised model release

- deactivate manifest through admin endpoint,
- remove mirror access,
- preserve evidence,
- publish signed replacement/advisory,
- inspect affected jobs,
- rotate release key if signer compromise is possible.

### Compromised host

Call `POST /admin/hosts/:deviceId/revoke` with the admin bearer and a bounded reason. This persists the revocation, removes artifact claims, clears the endpoint, rejects future signed requests, and requeues leased work. Also block at the edge and rotate any exposed job/API credentials. A signed multi-coordinator revocation feed and key-recovery protocol remain public-launch requirements.

### Encryption-key compromise

- stop submissions,
- restrict database access,
- rotate key through an explicit re-encryption migration,
- assess prompt/result/teaching exposure,
- follow the published breach process.

Changing `II_DATA_ENCRYPTION_KEY` without migration makes old ciphertext unreadable.

### Worker outage

Expired leases are requeued until `max_attempts`; then jobs fail. Monitor queued/leased age and ready workers by manifest. Do not increase retry limits indefinitely.

## Backups

Back up:

- SQLite database with a consistent SQLite backup method,
- release manifests,
- release public-key history,
- configuration excluding plaintext secrets where possible,
- audit and deployment records.

Restore tests must verify encrypted job readability using the corresponding key and must not send restored jobs to production workers.

## Monitoring

Minimum metrics:

- online signed hosts,
- ready workers by manifest,
- stored/routable expert coverage,
- queued/leased/completed/failed jobs,
- oldest queued age,
- lease expiry rate,
- inference duration by manifest/hardware class,
- signature and replay rejections,
- manifest/hash failures,
- teaching rejection counts by category (never text),
- checkout/provider failures.

Avoid high-cardinality device IDs and never attach prompts/results to telemetry.

## Scale transition

Before multiple coordinator replicas:

- replace in-memory rate limiting,
- move durable state to a transactional shared database,
- use a durable queue or safe database leasing primitive,
- coordinate lease expiry,
- centralize revocations,
- add idempotency keys,
- define migration/rollback.

A DHT is not a replacement for job durability, release trust, billing, or abuse response.
