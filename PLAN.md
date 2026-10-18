# II v2.0 — Implementation status and roadmap

## Project scope

II is a Python orchestration prototype for an optional local language model. No
base-model training, fine-tuning, or weight modification is performed by the
runtime tools. Model weights are not committed. LM Studio and direct GGUF use are
optional; demo mode does not generate model answers.

The repository retains root-level modules for compatibility. New `core/`,
`tools/`, `server/`, `cli/`, `graph/`, and `reasoning/` package facades do not
move or replace the existing public imports.

## Implementation status by phase

| Phase | Status | Implemented scope | Not implemented / not claimed |
|---|---|---|---|
| 1 — repository map, graph analysis, verification, evaluation harness | Implemented as bounded prototype | Python/JS/TS index, approximate local imports/calls, SCC/PageRank/clusters, static verifier, review-packet harness, 30-file synthetic cycle fixture | No semantic architecture proof, no model-quality benchmark, no asserted cycle-fix success rate |
| 2 — explicit constraints and activation/vector utilities | Partially implemented | Restricted formula parser, sequential one-clause callback interface, optional Z3, contrastive vector math, Gram-Schmidt, optional hook, TIES/DARE array operations, linear probe plumbing | NL translation is not automatic or validated; no precomputed vectors/probes; no active hidden-state integration in shipped backends |
| 3 — uncertainty search, repo ingestion, component catalog | Partially implemented | Generic entropy-triggered token-state adapter, GitHub shallow clone/license filter/JSON graph cache with CLI and local API routes, 10 original Jinja candidate components | Search is not integrated with the app or current model adapters; license filter is heuristic; no renderer; no component is approved |
| 4 — KV paging and full four-stage integration | Interface/stub only | 64-token min/max page metadata, approximate query ranking, page-tier bookkeeping, explicit paging interface | No RAM/VRAM transfer, no llama.cpp KV API integration, no constrained decoding, no complete 3-gate verifier |

## Current module map

- `ii_graph.py` retains the existing indexer and now records approximate local
  Python and relative JS/TS imports. `graph/topology.py` provides SCC, PageRank,
  clusters, and a bounded architecture summary. `graph/spectral_analysis.py` is
  a compatibility spelling; the algorithms are not eigenvalue-based spectral
  methods. `vendor/repomap_scc.py` is an adapter, not copied Aider code.
- `reasoning/constraint_compiler.py` validates explicit formulas and supports a
  sequential callback interface. The callback receives one clause at a time,
  but the compiler cannot establish that a model's translation preserves
  natural-language meaning. `ii_smt.py` uses a restricted AST compiler, never
  Python `eval`.
- `reasoning/verification_cascade.py` runs static Gate 1 checks. Gates 2 and 3
  require caller-supplied test/sandbox and entailment adapters. Skipped gates
  fail closed; no false-positive product or success probability is asserted.
- `vendor/orthogonal_steering.py`, `vibe/steering.py`, and `vibe/probes.py`
  provide optional vector/hook plumbing. They ship no model-specific artifacts;
  scores and steering effects are not calibrated or validated.
- `vendor/ties_merging.py` is optional NumPy array math that returns new arrays;
  it never loads or edits model weights. The referenced `arcee-ai/mergekit`
  repository reports LGPL-3.0, contrary to the supplied specification; no
  mergekit code is copied.
- `reasoning/entropy_mcts.py` requires an adapter with prefill, logits, token
  stepping, state cloning, and decoding. It is not connected to the shipped
  GGUF/LM Studio interfaces and makes no accuracy claim.
- `vendor/kv_pager.py` and `graph/kv_paging.py` maintain page metadata only.
  Actual KV transfer deliberately raises `NotImplementedError`.
- `ingestion/repo_ingestor.py` accepts public HTTPS GitHub owner/repo URLs,
  checks the root license text conservatively, parses supported source files,
  and writes a JSON graph cache. Cloned source is never executed.
- `blackboard/component_catalog/` contains ten original Flask/Jinja candidates,
  all marked `candidate` with review/build/a11y/visual gates pending. No renderer
  or React/TSX/Tailwind pipeline is implemented.
- `tests/benchmarks/` accepts optional `ground_truth`/difficulty metadata and
  creates human-review packets, including paired raw-model/II responses. The
  checked-in task list is empty; the CLI does not run models or auto-score truth.
- `ii_web.py` retains exact extracted passages and source URLs. Numeric/negation
  comparison is a lexical review signal, not contradiction proof or fact-checking.

## Four-stage pipeline status

1. **Pre-inference:** active approximate source graph/topology summary. KV page
   ranking works on supplied metadata only; no cache pages are fetched.
2. **Middle-layer interception:** vector arithmetic and optional PyTorch hooks
   are available for a compatible user-supplied model/module. No suitable hook,
   vectors, or probes are included for the current inference backends.
3. **Constrained decoding:** not integrated. The current backends do not expose a
   portable FSM/logit-masking interface; `llguidance` is not installed or wired.
4. **Post-generation:** static Python syntax/name/risk checks are integrated.
   The differential-test and entailment gates remain skipped without injected
   adapters. Local subprocess execution is not a sandbox.

## Validation scope

The test suite exercises fixtures and helper behavior, including a synthetic
30-file import cycle that the graph algorithm detects. This demonstrates a
narrow deterministic graph capability only. It does not compare a raw Qwen model
against II, establish that the model can repair the cycle, or measure broader
coding/research ability. No MMLU, GSM8K, HumanEval, SWE-bench, BFCL, or
Astra/Fable comparison is published.

No capability score, 2 GB RAM guarantee, false-positive rate, or 98.5% success
claim is made. Those values require a defined protocol and measurements; the
proposed gate-error multiplication assumes independence that has not been
established.

## Remaining roadmap

1. Provide a stable inference adapter exposing hidden states, logits, decoding
   constraints, and KV cache state before attempting end-to-end steering/MCTS/
   paging integration.
2. Measure peak resident memory on target hardware with the exact model file,
   context length, backend, and operating system.
3. Commission a reviewed benchmark dataset and human scoring protocol; compare
   raw model and pipeline runs with matched settings and publish limitations.
4. Review candidate component licenses and assets, then add build, unit, a11y,
   and visual checks before promoting any catalog item or writing a renderer.
5. Replace heuristic license detection with an explicit, reviewed license
   process before using ingestion results for redistribution.
6. Design genuine OS/container isolation before enabling any untrusted code test
   runner or exposing execution to users.

## License references

See [`docs/VENDOR_CREDITS.md`](docs/VENDOR_CREDITS.md). Vendor modules contain
original implementations of referenced ideas; the repository does not claim
third-party code extraction where none occurred.
