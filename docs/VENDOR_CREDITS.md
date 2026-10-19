# Vendor credits and licensing notes

The `vendor/` files in this repository are original II implementations of
small, published algorithmic ideas or interface adapters. **No source files
were copied from the projects below.** The names are credited as references,
not as endorsements or claims of code lineage. The implementation files are
covered by this repository's MIT license unless a separate notice is added.

| II file | Reference | License checked | Scope of reference |
|---|---|---|---|
| `vendor/repomap_scc.py` | [Aider-AI/aider](https://github.com/Aider-AI/aider) | Apache-2.0 | Repository-map design inspiration. II wraps its existing AST/tree-sitter indexer; no Aider source was copied. |
| `archive/vendor/orthogonal_steering.py` | [vgel/repeng](https://github.com/vgel/repeng) | MIT | Contrastive activation-vector and hook concepts. II's vector arithmetic and hooks are independently written. Archived in v3.0. |
| `archive/vendor/ties_merging.py` | [arcee-ai/mergekit](https://github.com/arcee-ai/mergekit) | **LGPL-3.0 in the checked repository metadata**, not Apache-2.0 | Algorithm reference only. No mergekit implementation code is copied or vendored. This corrects the license stated in the supplied specification. Archived in v3.0. |
| `archive/vendor/kv_pager.py` | [THUNLP/InfLLM](https://github.com/thunlp/InfLLM), [mit-han-lab/Quest](https://github.com/mit-han-lab/Quest) | MIT for the checked repositories | High-level paging/retrieval concepts only. II does not contain their cache code. Archived in v3.0. |

GitHub repository license metadata was checked during implementation for the
repositories above. License detection is not a legal opinion and repository
metadata can change. The ingestion filter is deliberately conservative and
rejects missing, ambiguous, or unrecognized root licenses; it is not a complete
SPDX parser.

## What is and is not vendored

- `vendor/repomap_scc.py` delegates to II's own `graph/ii_graph.py` and
  NetworkX.
- `archive/vendor/orthogonal_steering.py` implements numeric vector
  operations and an optional PyTorch hook; it ships no model-specific
  activation vectors. It is **archived** and not imported by the runtime.
- `archive/vendor/ties_merging.py` implements array operations only. It does
  not load, alter, or save model weights. It is **archived**.
- `archive/vendor/kv_pager.py` contains page summaries and a retrieval
  interface. Actual llama.cpp KV-cache transfer is `NotImplementedError`. It
  is **archived**.

## Third-party runtime dependencies

II v3.0 depends on the packages listed in `requirements-core.txt`
(Flask, Requests, NetworkX, trafilatura, ddgs, spaCy,
VADER, faiss-cpu, z3-solver, jsonschema, tree-sitter grammars and
llama-cpp-python). Each is distributed under its own licence; see the
upstream projects. Model weights are **not** bundled and remain under their
own licence and terms.

The 10 UI catalog entries are original Jinja fragments covered by this
repository's MIT license. They are marked `candidate` with license review and
visual/accessibility/build validation still pending. No third-party UI assets
are bundled.
