# Signed Model Manifest Protocol

`ii.model-manifest.v1` is the authority for model bytes accepted by an II coordinator or worker. A URL, filename, model-family name, or IPFS CID alone is never sufficient.

## Goals

- bind model identity to exact bytes,
- prevent mirror or coordinator substitution,
- record runtime/role compatibility,
- separate release authorization from host self-reporting,
- provide a stable unit for jobs and learning contributions,
- support content-addressed storage and authenticated local-reference peer transfer.

## Required payload

```json
{
  "schema": "ii.model-manifest.v1",
  "model_id": "ii/reviewed-worker",
  "version": "1.0.0",
  "role": "worker",
  "runtime": "node-llama-cpp@3.5.0",
  "license": {
    "id": "Apache-2.0",
    "redistribution_confirmed": true,
    "source_url": "https://model-card.example"
  },
  "created_at": "2026-10-22T00:00:00.000Z",
  "artifacts": [
    {
      "id": "worker-q4",
      "sha256": "64 lowercase hexadecimal characters",
      "size_bytes": 123,
      "format": "gguf",
      "url": "https://controlled-mirror.example/model.gguf"
    }
  ]
}
```

Roles:

- `worker`: complete executable model used by the durable job system,
- `expert`: one model-topology artifact with required `expert_id`,
- `phone`: local mobile artifact; not a desktop worker claim.

Formats currently recognized by the protocol are `gguf`, `safetensors`, `mlc`, and `other`. Recognition is not runtime support. v0.4 desktop inference executes compatible complete GGUF only.

## Signature envelope

The release signer adds:

```json
{
  "manifest_id": "sha256(canonical unsigned payload)",
  "publisher_public_key": "base64 Ed25519 SPKI",
  "signature": "base64 Ed25519 signature"
}
```

Canonicalization recursively sorts object keys, preserves array order, omits envelope fields, and emits JSON scalar forms. Implementations live in `protocol/crypto.js` and `protocol/model-manifest.js`.

## Trust

The verifier must compare `publisher_public_key` with an independently configured `II_RELEASE_PUBLIC_KEY`. Accepting any self-declared public key would only prove that an attacker signed their own malicious manifest.

## Publication

```bash
cd network
npm run keygen:release -- ./release-key.pem
II_RELEASE_PRIVATE_KEY_PATH=./release-key.pem \
  npm run sign:manifest -- unsigned.json signed.json
curl -X POST http://localhost:8787/admin/models \
  -H "Authorization: Bearer $II_ADMIN_API_TOKEN" \
  -H 'Content-Type: application/json' \
  --data-binary @signed.json
```

Do not commit `release-key.pem`. The repository ignores model binaries but operators must also secure signing keys outside the source tree.

## Host claim validation

A host artifact claim repeats only signed values:

```json
{
  "manifest_id": "...",
  "artifact_id": "worker-q4",
  "sha256": "...",
  "size_bytes": 123,
  "format": "gguf",
  "inference_ready": true
}
```

The coordinator rejects any mismatch. It derives capacity from the manifest size. Expert claims additionally repeat exact `expert_id`.

## Deactivation and rollback

`POST /admin/models/:manifestId/deactivate` disables routing/leasing and fails pending jobs for that release. A rollback is a newly active, previously signed manifest; mutable tags such as `latest` are not trusted identities.

## Future v2 fields

A backward-incompatible future schema should add only after tests exist:

- per-chunk hashes or a Merkle root,
- model tensor topology,
- quantization details,
- tokenizer artifact/hash,
- minimum RAM/VRAM,
- benchmark report hash,
- safety-card hash,
- supersedes/rollback relationships,
- adapter ABI and compatible base hash,
- multiple mirrors and peer CIDs.

Do not overload v1 fields with unverifiable marketing metadata.
