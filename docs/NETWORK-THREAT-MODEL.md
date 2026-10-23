# II Network Threat Model

Status: v0.4 software boundary. This covers `network/`, `client/`, `protocol/`, `web/`, `p2p/`, and `distributed/`. It is not an independent audit or certification.

## Assets

- release and adapter-signing private keys;
- host/peer private keys;
- model/adapter artifacts and manifests;
- job prompts/results and teaching/review content;
- browser-vault conversations and passphrases;
- job/teaching/lease/identity/API tokens;
- Dodo API and webhook credentials and provider payloads;
- user identity, subscription, entitlement, audit, fraud, and Genesis data.

## Trust boundaries

| Boundary | Verified | Not established |
|---|---|---|
| Release | Ed25519 publisher, canonical payload, exact artifact metadata | quality, malware absence, license rights |
| Host | Ed25519 key possession, signed claims/replay window | physical machine uniqueness, honest hardware/location, correct inference |
| Worker result | challenge/duplicate observations and score history | confidential computing or universal semantic correctness |
| Coordinator | process, DB, configured encryption keys | safety after process/key compromise |
| Browser vault | Web Crypto ciphertext while locked | protection from active XSS/browser/OS compromise or weak passphrase |
| Peer transfer | peer signature, bounded range, final hash | public NAT reachability, peer availability, safe operated relay |
| Activation reference | HMAC, replay/shape/hash/timeout checks | encryption, transformer correctness, physical LAN performance |
| Learning | consent, deterministic scan, review/provenance/gates/signature states | completeness of scanner or truth of external benchmark/privacy reports |
| Dodo | exact Standard Webhooks signature, replay/idempotency/correlation | live provider operation, accounting/legal correctness |

## Implemented controls

### Release poisoning

- deterministic canonical JSON and manifest IDs;
- Ed25519 verification against configured trusted public key;
- signed URL/hash/size/format/role/runtime/expert metadata;
- download SHA-256 and content-addressed storage;
- manifest deactivation and host revocation.

Signatures identify an authorizer. They do not prove legal redistribution, model safety, or benchmark quality.

### Host spoofing/replay and renderer isolation

- device IDs derive from Ed25519 SPKI fingerprints;
- signed registration, heartbeat, lease, status, completion, and failure actions;
- five-minute clock and strictly increasing consumed timestamp;
- persistent revocation removes claims and cancels/requeues attempts;
- private key is a separate `0600` file and never enters Electron renderer JavaScript;
- Electron uses context isolation, sandbox, no Node integration, narrow preload API, blocked navigation, and CSP.

Key rotation remains a new identity; signed ownership transfer/recovery is not implemented.

### Cross-job state leakage

Every job creates a new llama context sequence and chat session. Session/context disposal runs in `finally`; model teardown waits for the serialized request queue. Only model weights remain shared. Tests assert separate contexts and disposal.

Residual risk: runtime/library bugs, GPU memory reuse below the API boundary, process compromise, or accidental plaintext worker logging. No confidential-computing claim is made.

### False capacity and malicious output

- desired assignments, hosted artifact claims, runtime readiness, and measured observations are distinct;
- claim metadata must match an active signed artifact;
- leasing requires exact manifest and readiness;
- capacity/load/benchmark/region remain labelled self-declared;
- deterministic challenges, duplicate hashes, bounded hedging, decay, and quarantine provide observations;
- conflicts expose hashes, not plaintext, to normal admin listing.

Two disagreeing workers do not reveal which is malicious, so both are temporarily quarantined. A coordinated Sybil group can still manipulate majority-style checks. Hardware attestation, stronger semantic verification, challenge secrecy/rotation, and identity/Sybil controls remain external work.

### Durable private data

- jobs, results, attempt outputs, teachings, review notes, Dodo payloads, and push details use AES-256-GCM with row/context-specific AAD;
- data key is not stored in SQLite;
- tokens/correlations are hashed;
- result metrics use a numeric allowlist;
- logs and list routes avoid prompt/result/payment plaintext;
- stronger contribution scanning rejects common direct identifiers/secrets before storage;
- contributor withdrawal tombstones source text and stops affected rollout.

The coordinator can decrypt work, and the selected worker sees its prompt. This is not end-to-end encryption against coordinator/workers. Published model weights cannot be selectively untrained by deleting a database row.

### Browser vault/PWA

- PBKDF2-SHA-256 (310,000 iterations) derives a non-extractable AES-GCM key;
- random salt/IV and per-record AAD;
- passphrase/key not persisted;
- API token in tab-scoped session storage;
- service-worker static allowlist excludes all authenticated APIs;
- rendered conversation text uses `textContent`.

Residual risk includes malicious same-origin script, compromised deployment/CDN, browser extension, unlocked device, weak passphrase, browser/OS bugs, and storage eviction. CSP and encryption at rest do not protect an unlocked active page from same-origin code.

### Peer transfer

- Ed25519 request authentication binds method/path/range/body hash, timestamp, and nonce;
- replay cache and bootstrap-signed directory;
- HTTPS except explicit loopback development;
- bounded metadata/body/range/artifact size, concurrency, and peer-byte budget;
- partial fsync/resume and final SHA-256;
- corruption moves to rejected storage and emits a reputation event;
- relays can fetch only discovered content-addressed peers.

Residual risk: directory/relay availability, traffic analysis, disk exhaustion within configured limits, malicious-but-valid slow peers, Sybil reports, public endpoint abuse, and untested NAT. The local reference does not implement general SSRF-safe arbitrary URL fetch; operator bootstrap/relay/fallback configuration must remain trusted and HTTPS.

### Identity, billing, and webhook handling

- production passwordless tokens leave only through a configured delivery adapter; test return requires an explicit test flag;
- scoped/expiring/revocable tokens stored as hashes;
- authenticated checkout correlation uses an opaque secret stored as a hash;
- Dodo signature covers exact raw body, message ID, and timestamp;
- five-minute replay window and provider-event uniqueness/payload hash;
- encrypted event payload, state transaction, audit, and fraud flags;
- adverse refund/dispute states suspend entitlements.

Residual risk: compromised email/delivery provider, Dodo account/key, metadata stripping on pre-created links, out-of-order provider events, reconciliation gaps, account takeover, chargeback abuse, tax/accounting errors, and legal obligations. Production needs live sandbox/reconciliation tests and operator review.

### Learning/release gates

- audited plaintext views;
- scan repeated before approval/dataset use;
- approved-only single-manifest dataset;
- immutable content/provenance hash;
- secret-scanned bounded training metadata;
- benchmark/privacy/artifact gates;
- distinct approval and signing transition;
- staged rollout/rollback/withdrawal.

External runners can lie in submitted evidence. Production must verify signed raw reports and artifact bytes independently. The scanner is not an anonymizer, and a human reviewer can mishandle plaintext.

## Central residual risks

### Coordinator/key compromise

The coordinator holds data and provider secrets. Process compromise can decrypt work and alter state. Use secret management, least privilege, restricted admin access, consistent backups, restore tests, key rotation/re-encryption procedures, and independent audit.

### Metadata privacy

IP, country/declared region, timing, manifest, queue/load, artifact, billing, and job-size metadata remain visible. Pseudonymous IP hashes still become personal data when linkable.

### Single-process SQLite

WAL survived the automated process-kill test, but one coordinator is a failure/scale boundary. In-memory registration rate limits reset on restart. Multi-replica operation needs a shared transactional DB/queue, distributed rate limit, globally atomic attempts, revocation propagation, and tested disaster recovery.

### Sybil/Genesis

A cryptographic identity is not one person/device. Genesis software qualification requires user plus verified first-400 host, but reward eligibility still requires legal identity, anti-fraud, uptime, terms, tax, and appeals policy.

### Dependency lifecycle

The project constraint pins Express 4.19, Electron 28, and current native/runtime packages despite known advisory pressure. Production release management must review supported upgrades; do not blindly apply incompatible `npm audit --force` output.

## Operational requirements

- keep release/adapter private keys offline or in protected signing infrastructure;
- distribute trusted public keys out of band;
- use distinct high-entropy secrets and synchronized clocks;
- terminate TLS at trusted infrastructure with exact proxy/CORS policy;
- never expose the loopback llama port directly;
- back up DB/manifests/audit and test restore with corresponding keys;
- rotate/re-encrypt deliberately—changing the data key alone destroys readability;
- monitor signature, replay, conflict, challenge, expiry, webhook, quota, and fraud events without content;
- maintain incident, model-removal, account/payment, contributor-withdrawal, and breach runbooks.

## Public-launch security gates

Independent review, host key recovery, multi-coordinator revocation/rate limits, challenge/Sybil hardening, operated peer/relay review, verified training evidence, live Dodo/reconciliation, secret-manager deployment, encrypted restore proof, DPDP/privacy documentation, and abuse/support response remain mandatory.
