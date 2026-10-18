# archive/ — quarantined scaffolding (II v3.0)

Everything in this directory is **dead scaffolding**: code that exists, is
importable, and is exercised by a few unit tests, but that the shipped
runtime cannot use with the backends II actually ships.  It was moved here in
Phase 0 of the "Pure II" plan so that the active package tree contains only
code that does something real.

Nothing in `archive/` is imported by the runtime.  The modules keep working
for anyone who wants to read them, and `archive/tests/` still holds the unit
tests that covered them.

---

## What was moved here, and why

| Module | What it does | Why it is archived |
|---|---|---|
| `ii_vision.py` | MobileCLIP-style image/text similarity. | The endpoint returns **501**: it computes whole-image similarity and does *not* localise UI regions. It needs `torch` + `transformers` (multi-GB) and would break the 2 GB RAM budget. Not part of Pure II. |
| `vibe/steering.py` | Activation steering: named style slots, vector load/save, optional PyTorch pre-hook. | The shipped GGUF and LM Studio adapters expose **no hidden-state hook**, so no vector can ever be applied. No calibrated vectors are bundled, and the "style names" are slots, not trained artefacts. |
| `vibe/probes.py` | Linear probes over hidden states. | Same reason: no compatible hidden-state hook exists in the shipped inference backends, and no probe weights are bundled. |
| `vendor/orthogonal_steering.py` | Contrastive vector maths, Gram-Schmidt orthogonalisation, optional torch hooks. | Pure array maths that never loads or modifies model weights. It was scaffolding for the steering experiment above. |
| `vendor/ties_merging.py` | TIES / DARE task-vector merging maths. | It operates on NumPy arrays only; it never loads or edits model weights, and no task vectors are shipped. |
| `vendor/kv_pager.py` | 64-token KV page summaries and retrieval metadata. | Metadata only. Actual RAM/VRAM transfer deliberately raises `NotImplementedError`; there is no llama.cpp KV-cache integration. |
| `graph/kv_paging.py` | Thin wrapper around `vendor/kv_pager.py`. | Same reason as above. |
| `reasoning/entropy_mcts.py` | Entropy-triggered MCTS over a token-state adapter. | It requires an adapter exposing prefill, logits, token stepping, state cloning and decoding. No shipped backend provides one, and it is not wired into the app. |

## What was **not** archived, and why

* `vendor/repomap_scc.py` — a real adapter around II's own symbol indexer
  (SCC detection, PageRank, clusters). It works and is not scaffolding.
* `blackboard/component_catalog/` — ten original Jinja component *candidates*.
  They are not approved and have no renderer, but they are real, reviewable
  source files, not dead code.
* `reasoning/constraint_compiler.py`, `reasoning/verification_cascade.py` —
  the restricted formula parser and the fail-closed verification cascade are
  active and tested.

## Honesty rules that apply to this directory

* No archived module may be described as "working" in the README or in the
  UI.  Their docstrings say exactly what they do and do not do.
* Nothing here is a claim about model behaviour: no probe accuracy, no
  steering effect, no merge quality.
* If any of these modules is ever revived, it must first pass the same
  honesty rules as the rest of the project (see `README.md` §1.4 in the
  project plan: no claim that an invention was verified, no "infinite"
  branding, no unproven percentages).

## Running the archived tests

```bash
python -m pytest archive/tests -q
```

They are excluded from the main suite on purpose: they document behaviour of
code that the product does not ship.
