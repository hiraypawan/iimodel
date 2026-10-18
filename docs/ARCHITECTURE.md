# II architecture (prototype)

II is a Python application that connects an optional user-supplied local model to
a Flask UI and helper tools. No model weights are checked in. Existing root
modules remain the source of truth; `core/`, `tools/`, `server/`, `cli/`, and
`reasoning/ii_*` files are compatibility facades rather than a disruptive move.

## Request and repository path

1. `app.py` selects GGUF when `llama-cpp-python` and a model file are present,
   otherwise tries LM Studio, then a non-generative demo fallback.
2. `/api/chat` passes messages and tools to the model. Tool selection is
   model-dependent; a keyword hint is non-binding. `ii_agent.py` pins an
   approximate repository index and topology summary before inference.
3. `ii_graph.py` uses Python AST and optional tree-sitter JS/TS grammars. It
   records file/symbol definitions, some local imports, and unambiguous call
   references. Dynamic behavior and unresolved edges are omitted.
4. `graph/topology.py` provides SCCs, personalized PageRank, file clustering,
   and a bounded summary. `graph/spectral_analysis.py` is a compatibility name;
   it does not implement eigenvalue-based spectral analysis. The graph is a
   navigation aid, not an architecture proof.
5. `ii_web.py` performs external search/fetch requests. It retains source URLs
   and exact extracted substrings, and can produce heuristic conflict signals.
   None of these operations verifies factual support.
6. `reasoning/constraint_compiler.py` parses bounded explicit formulas. A
   caller-supplied translator can be invoked once per decomposed prose clause,
   but translation fidelity is unverified. `ii_smt.py` uses Z3 optionally and
   compiles expressions from a restricted AST without Python `eval`.
7. `reasoning/verification_cascade.py` always supports static Gate 1 checks.
   Gate 2/3 require external caller-supplied adapters. Missing gates are skipped
   and the full cascade does not pass. No probability or independence claim is
   attached to gate results.
8. `blackboard/component_catalog/` contains ten original Flask/Jinja candidate
   fragments. There is no renderer and all components remain unapproved.
9. `tests/benchmarks/eval_runner.py` builds human-review packets from saved
   responses. The dataset is empty; the harness does not run a model or score
   factual correctness automatically.

## Experimental model-side utilities

- `vendor/orthogonal_steering.py` provides optional contrastive vector math and
  PyTorch layer-input hooks. No model-specific vectors are included. The shipped
  GGUF and LM Studio adapters do not expose a compatible layer hook.
- `vibe/probes.py` can score user-supplied linear probes but ships no calibrated
  probes and does not infer user intent.
- `vendor/ties_merging.py` performs optional NumPy array arithmetic only. It does
  not load or modify model weights and is not connected to chat inference.
- `reasoning/entropy_mcts.py` expects an external adapter with prefill/logit/
  token-step/state-clone methods. It is not connected to shipped backends and is
  best understood as a bounded rollout interface, not a validated performance
  feature.
- `vendor/kv_pager.py` and `graph/kv_paging.py` retain 64-token page summaries
  and rankings. Actual llama.cpp KV transfer is unimplemented and raises
  `NotImplementedError`.
- `ingestion/repo_ingestor.py` shallow-clones only HTTPS GitHub owner/repo URLs,
  applies a conservative root-license text filter, parses files as data, and
  writes a JSON graph cache. It never executes cloned source.

## Four-stage integration status

| Stage | Current state |
|---|---|
| 1. Pre-inference | Approximate repository map and graph topology are computed. KV paging is metadata-only. |
| 2. Middle-layer | Vector/probe utilities exist for compatible external modules; no vectors are bundled and current adapters expose no portable hidden-state hooks. |
| 3. Constrained decoding | Not integrated; no FSM/logit-masking interface is wired to the current backends. |
| 4. Post-generation | Static preflight is available. Test execution and binary-entailment gates require caller adapters and are not enabled by default. |

## Trust and resource boundaries

- Model output, JSON, retrieved web text, and cached graphs are untrusted input.
- `execute_code` defaults to a resource-limited subprocess running with
  the current OS user's permissions (`II_CODE_SANDBOX=docker` provides
  fail-closed Docker isolation; see `SECURITY.md`). A subprocess is not a
  sandbox. `II_PRIVATE=1` disables all network tools.
- License classification is heuristic and is not legal advice.
- The Flask development server has no authentication and should remain on a
  trusted local machine.
- No under-2-GB memory guarantee is made. Peak memory depends on model file,
  quantization, context, backend, and platform; it has not been measured.

See the Limitations section of the root [`README.md`](../README.md), [`SECURITY.md`](SECURITY.md), and [`VENDOR_CREDITS.md`](VENDOR_CREDITS.md)
for scope, caveats, and licensing references.

