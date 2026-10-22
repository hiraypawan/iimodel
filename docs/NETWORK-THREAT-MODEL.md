# II Network Threat Model

Status: v0.3 implementation boundary. This document covers `network/`, `client/`, and `protocol/`; it does not certify the earlier local Python research engine.

## Assets

- release-signing private key,
- host private keys,
- model artifacts and manifests,
- prompts and results,
- consented teaching pairs,
- job/teaching receipt tokens,
- payment and administrator credentials,
- Genesis applicant data.

## Trust boundaries

| Boundary | Trusted today | Not trusted |
|---|---|---|
| Release publication | Configured Ed25519 publisher key | Artifact mirrors and arbitrary manifests |
| Host identity | Possession of registered Ed25519 key | Host-reported performance and execution correctness |
| Coordinator | Operator process and encryption key | Public API clients |
| Worker execution | Exact artifact identity and signed host | Prompt confidentiality and result correctness |
| Teaching intake | Explicit consent + basic scanner | Claim that an adapter is safe or private |
| Dodo | Authenticated provider API response | Browser-supplied pricing or entitlement claims |

## Implemented controls

### Release poisoning

- Manifest payloads use deterministic canonical JSON.
- `manifest_id` is SHA-256 of the unsigned payload.
- Ed25519 signatures are verified against independently configured `II_RELEASE_PUBLIC_KEY`.
- Artifact URL, size, format, hash, role, runtime, and expert ID are covered by the signature.
- Clients hash downloaded bytes before use and store expected-hash artifacts in content-addressed paths.
- An operator can deactivate a release; queued and leased jobs for it fail closed.

A release signature proves only which publisher authorized bytes. It does not prove benchmark quality, absence of malware, safety, or legal redistribution rights.

### Host spoofing and replay

- Device IDs derive from Ed25519 public-key fingerprints.
- Registration, heartbeat, lease, completion, and failure bodies are signed.
- Every mutable body field is signature-bound.
- Requests outside a five-minute clock window are rejected.
- Timestamps must increase, preventing replay after consumption.
- Private keys are separate `0600` files; renderer JavaScript never receives them.

An administrator can revoke a device ID, immediately remove its artifact claims, reject future signatures, and requeue its leased work. Key rotation still creates a new identity; a signed ownership-transfer protocol is not implemented.

### False capacity

- Desired assignments and hosted artifacts are separate tables.
- A host claim must exactly match an active signed artifact.
- Capacity uses signed-manifest artifact size, not arbitrary client bytes.
- Routable expert coverage requires signed identity, live heartbeat, official artifact, readiness, and endpoint.
- Worker leasing requires a matching active worker manifest marked ready.

Readiness remains self-attested. A malicious worker can load nothing and claim readiness. Remote challenge execution and result verification are still required.

### Prompt and correction storage

- Durable prompt/result/correction fields use AES-256-GCM.
- `II_DATA_ENCRYPTION_KEY` is never stored in the database.
- Receipt and lease tokens are stored as SHA-256 hashes.
- Teaching intake rejects common emails, Indian phone numbers, Aadhaar-like numbers, private keys, API-key patterns, and bearer tokens before storage.
- Teaching content is quarantined and never auto-trained.
- Contributors can delete accepted intake with their receipt token.

The coordinator decrypts prompts to lease them. A selected worker receives plaintext. This is not confidential computing or end-to-end encryption against workers.

### Electron

- Renderer Node integration is disabled.
- Context isolation and Chromium sandbox are enabled.
- A narrow preload bridge exposes only four host operations.
- External navigation is blocked; HTTPS links open in the OS browser.
- CSP disallows inline renderer scripts.

### Public APIs

- Model publication, revocation, and teaching metadata use `II_ADMIN_API_TOKEN`.
- Genesis CSV export uses a separate `ADMIN_EXPORT_TOKEN`.
- Job submission and teaching submission use separate bearer secrets.
- Job/teaching reads require unguessable receipt tokens.
- Registration is throttled by IP.
- Request bodies are size-limited.
- CSV export neutralizes spreadsheet formulas.

## Important residual risks

### Malicious workers

Workers can inspect prompts and return plausible garbage. Mitigations not yet implemented:

- deterministic canary challenges,
- duplicate execution and comparison,
- signed benchmark attestations,
- output verification,
- reputation with decay,
- operator revocation lists,
- hardware-backed attestation.

Never send secrets or unredacted private documents to an untrusted community worker.

### Metadata privacy

The coordinator observes device IP, country, timing, model version, queue activity, and job sizes. Encryption at rest does not hide this metadata.

### Coordinator compromise

The coordinator holds the data encryption key and can decrypt durable work. Production must use a secret manager, restricted service identity, backups, rotation, and audited access. A database backup without the key protects content; a process compromise with the key does not.

### NAT/public endpoint exposure

Pull jobs require only outbound HTTPS. Direct `/route` expert endpoints require an operator-managed TLS reverse proxy and request authentication, which v0.3 does not yet provide. Do not expose port 7777 directly to the internet.

### Sybil and Genesis abuse

Cryptographic identity does not equal one human or one physical machine. Genesis numbering and rewards require independent verification, uptime rules, revocation, and legal terms.

### SQLite and in-memory limits

The coordinator is a single failure domain. Registration rate limits are in memory and reset on restart. SQLite is appropriate for the current pilot, not a horizontally scaled public control plane.

### Dependency risk

The locked Electron 28 and Express 4.19 stack may have published advisories. They are retained for compatibility with the project constraint. Production release management must evaluate upgrades rather than applying unreviewed `npm audit --force` changes.

## Key operations

- Keep the release private key offline; sign manifests in a restricted release process.
- Distribute the release public key out-of-band.
- Rotate API and encryption credentials through a secret manager.
- Back up the database and test restore.
- Deactivate a compromised manifest immediately.
- Revoke compromised host identities once a revocation endpoint exists; until then remove the device row and block it at the edge.
- Never log plaintext jobs or teachings.

## Security exit criteria for public launch

- independent security review,
- host-key rotation/recovery and a signed distributed revocation feed,
- distributed rate limiting,
- authenticated worker transport,
- readiness/result verification,
- Dodo webhook verification,
- secret-manager deployment,
- encrypted tested backups,
- abuse response runbook,
- India DPDP review and published privacy policy.
