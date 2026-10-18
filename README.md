# II v2.0 — Local Tool-Augmented Agent Prototype

II is a local tool-augmented agent prototype developed by Pawan Hiray (identity: II v2.0). It connects an optional local language model (Qwen2.5-1.5B GGUF or LM Studio) to a Flask chat UI with web search, code execution, graph analysis, and steering helpers. When no LM Studio server is running, it operates in **agentic mode**: uses real-time internet search (Brave / SearXNG / DuckDuckGo / GitHub / Headless) and synthesizes answers directly from search results — no model backend required.

## What is implemented

| Component | Current scope and limitations |
|---|---|
| Local chat | Uses `llama-cpp-python` with a user-provided GGUF or an OpenAI-compatible LM Studio server. With neither available, the app reports demo mode and does not generate model answers. |
| Repository map | Indexes Python, JavaScript, TypeScript, JSX, and TSX declarations; resolves some local imports and unambiguous calls. Edges remain approximate. |
| Graph analysis | Computes SCCs, personalized PageRank, and file clusters. It is a navigation aid, not a validated architecture model. A synthetic 30-file import-cycle fixture tests cycle detection; no raw-model-vs-II benchmark has been run. |
| Verification | Static syntax/name/risk checks are available by default. Optional Gate 2/3 hooks require caller-supplied test and entailment adapters; skipped gates fail the full cascade. No code is executed by the default verifier. |
| Constraints | A restricted explicit-formula parser can feed optional Z3. Sequential prose decomposition is heuristic; translation requires a caller-supplied adapter and does not guarantee a correct formalization. |
| Steering/probes | Vector math, optional PyTorch hooks, and linear-probe plumbing are present. No model-specific vectors or probes are bundled, and current inference adapters do not expose compatible hidden-state hooks. |
| TIES/DARE | Optional NumPy task-vector arithmetic returns new arrays; it does not load or modify model weights. |
| KV paging | 64-token page summaries and retrieval metadata are implemented. Actual RAM/VRAM transfer and llama.cpp KV-cache integration deliberately raise `NotImplementedError`. |
| Repo ingestion | Shallow GitHub clone, conservative root-license text filter, approximate graph construction, and JSON graph cache. Cloned code is parsed as data, never executed. License detection is not legal advice or a full SPDX parser. |
| UI catalog | Ten original Flask/Jinja component **candidates** are included. None is approved; there is no renderer or generated-UI build pipeline. |
| Evaluation | Produces human-review packets for supplied responses and supports raw-vs-II side-by-side packets. The task dataset is empty; the harness does not run models or auto-score factual correctness. |
| Evidence | URL fetching retains source URLs and extracted passages. Numeric/negation conflict flags are rough review signals, not fact-checking. |
| Code execution | Runs Python with the current user's permissions. It is **not sandboxed**; do not expose this app to untrusted users. |
| Vision | Reliable UI-region localization is not implemented; the endpoint remains disabled. |

No scalar capability rating, 2 GB RAM guarantee, error-rate estimate, or general
model-superiority claim is made. Memory depends on the selected model, backend,
context size, and runtime configuration; it has not been measured here.

## Architecture and zero-training scope

No base-model weights are modified or trained. The available pieces form a
partial, capability-gated pipeline rather than the fully integrated four-stage
system proposed in the original research plan:

```text
1. PRE-INFERENCE     Active: approximate repository index + topology summary
                     Metadata only: KV-page summaries; no cache transfer
2. MIDDLE LAYERS     Optional utilities: contrastive vectors, Gram-Schmidt,
                     readout probes. No bundled vectors and no active-backend hook.
3. DECODING           Not integrated: current model adapters expose no portable
                     constrained-decoding/FSM interface. Entropy search requires
                     a separate token-state adapter and is not wired to the app.
4. POST-GENERATION    Active: static Python preflight. Full cascade is fail-closed
                     unless caller supplies test/sandbox and entailment adapters.
```

The `spectral` CLI spelling is retained as a compatibility alias for the
approximate topology summary. The algorithms used are SCC detection, PageRank,
and community clustering—not eigenvalue-based spectral graph methods.

## Quick start (recommended: LM Studio or agentic mode)

**Agentic mode** (no LM Studio required): uses internet search (Brave / SearXNG / DuckDuckGo / GitHub API / Headless browser) and synthesizes answers directly from real-time results. Works with `.venv311` (Python 3.11) without a GGUF file.

1. Install LM Studio, load a compatible local GGUF, and start its OpenAI-compatible
   server at `http://127.0.0.1:1234`.
2. Install Python 3.11 and the dependencies:

   **Windows (PowerShell):**
   ```powershell
   py -3.11 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install --upgrade pip
   pip install -r requirements.txt
   python app.py
   ```

   **macOS/Linux:**
   ```bash
   python3.11 -m venv .venv
   . .venv/bin/activate
   python -m pip install --upgrade pip
   pip install -r requirements.txt
   python app.py
   ```

3. Open `http://127.0.0.1:5000/`; check `GET /api/status` for backend mode.

The Windows launchers install the core requirements and start the app; they do
not download model weights. Direct GGUF use requires the optional
`llama-cpp-python` package and a separately downloaded model. Additional optional
integrations are listed in `requirements-optional.txt`. Development tools are
listed in `requirements-dev.txt`.

## CLI and API

```bash
python main.py graph
python main.py topology                 # `spectral` is a compatibility alias
python main.py verify --input path/to/file.py
cat path/to/file.py | python main.py verify
python main.py verify --full-cascade < path/to/file.py
python main.py steer --action list
python main.py ingest --repo https://github.com/OWNER/REPO
python main.py benchmark --dataset path/to/reviewed_tasks.json --responses run.jsonl
python main.py chat
```

The verifier reads a named file when `--input` points to one; otherwise it reads
stdin through EOF. Static verify prints machine-readable JSON and exits nonzero
on a failed check. Full-cascade mode currently fails closed because the default
runner and entailment adapters are not configured. Steering list shows proposed
style names separately from available vector files; applying a vector is not
supported by the shipped inference adapters.

The Flask app exposes `/api/graph`, `/api/graph/topology`, `/api/blackboard/graph`,
`/api/repos/ingest`, `/api/repos/graph`, `/api/verify`, and `/api/steer` in addition
to chat and helper routes. Repository API ingestion is a blocking local Git clone
with conservative resource caps; the API has no authentication and must not be
exposed to untrusted clients. See
[`docs/API.md`](docs/API.md) for the current API contract.

## Tests and validation

```bash
python -m compileall -q -f .
python _test_graph.py
python _test_full.py
python -m unittest discover -s tests -v
python blackboard/component_catalog/validate_catalog.py
```

Tests cover synthetic graph/index fixtures, including a 30-file circular import
fixture, plus helpers and Flask routes. They do not measure model quality. Real
GGUF inference and a live LM Studio server have not been validated in the
current test run.

## Limitations, sources, and safety

Read [`docs/LIMITATIONS.md`](docs/LIMITATIONS.md) for deferred features and
unmeasured claims. [`docs/VENDOR_CREDITS.md`](docs/VENDOR_CREDITS.md) records
license references and clarifies that no third-party source code was copied into
`vendor/`. The project code is licensed under MIT; see [`LICENSE`](LICENSE).
Model weights remain subject to their own license and terms.

Web search and URL fetch contact external services and sites. Local inference can
stay on-device when configured locally and when web tools are not used, but the
whole system is not universally free, private, or air-gapped. The development
server has no authentication and should remain on a trusted local machine.
