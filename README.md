# II v3.0 — Pure Internet Intelligence (Deterministic Reasoning System)

**II = Internet Intelligence.** II is a local, deterministic reasoning system
where "thinking" means *Python algorithms + live internet data + graph
traversal + Z3 solving + DAG orchestration*. **The LLM never reasons.**

II is a **truth instrument**, not a thinking companion.

---

## The 1.5B LLM is the renderer

> The 1.5B LLM is the renderer: it formats structured JSON into natural
> language and generates sandbox-verified code snippets. All reasoning, logic,
> and conclusions come from deterministic Python algorithms.

The local model (Qwen2.5-1.5B GGUF or an LM Studio server) does exactly two
things:

1. **Formatting** — turn the structured JSON produced by the algorithms into
   natural Hinglish/English. The system prompt says, verbatim:
   *"Stick strictly to the provided JSON. Do not add facts, ideas, or
   conclusions."*
2. **Constrained code generation** — short snippets (simulations, tool code,
   bug fixes) from precise specs. **All generated code is sandbox-verified
   before use.**

Forbidden for the model: open-ended ideation, factual claims, logical
conclusions, brainstorming, opinions.

Everything the model writes is checked by a **hallucination detector**
(entity diff). A rejected rendering is discarded, retried once, and finally
replaced with a dump of the raw JSON — the answer is never silently kept.

---

## The status taxonomy (exact labels only)

| Status | Meaning |
|---|---|
| `VERIFIED` | Every factual claim is backed by **≥ 2 independent live URLs**, with no unresolved conflicts. |
| `PARTIAL` | Some steps were resolved, the final answer was not. **Always shows the work done and what is missing.** |
| `SIMULATED` | A hypothesis was tested in a sandbox toy model. **Always** includes the baseline, the assumptions, and a disclaimer. |
| `UNVALIDATED_COMBINATION` | A brainstorming output. The components are cited; **the combination is not validated**. |
| `CONFLICT` | Sources disagree. Both sides are shown with URLs. **No answer is forced.** |
| `INSUFFICIENT_DATA` | Fail-closed. Nothing was invented to fill the gap. |

**Fail-closed wins.** The system never returns an empty answer: it returns
`PARTIAL` (steps taken + sources found) and states exactly what is missing.
"No blank page paralysis" means *show your work*, not *invent an answer*.

---

## Honest capability statement

II is designed to be stronger than large AI models at:

* **live, cited facts** — every claim carries its URL,
* **hallucination-resistant Q&A** — fail-closed by design; every claim
  is cited and entity-checked,
* **local repository analysis** — the index, topology and verification run on
  your machine,
* **privacy** — the model runs locally; the Exocortex stores only what you
  approve,
* **cost** — no model API costs; search backends have free tiers and then
  paid tiers,
* **debuggability** — every stage is a Python function you can read and run.

II does **NOT** match large AI models at:

* nuanced reasoning,
* creative writing,
* open-ended conversation,
* complex code architecture,
* taste or judgement.

**Do not claim otherwise anywhere.** Simulations prove nothing about the real
world. No capability score, no proven-efficiency percentage and no
zero-hallucination figure is published by this project, because none of them
has been measured in a way that would justify the number.

---

## The II Way vs The AI Way

| | The II Way | The AI Way |
|---|---|---|
| Where the answer comes from | Deterministic algorithms over live sources | Model weights |
| Citations | Every factual claim carries a URL | Optional, often absent |
| When the model is wrong | The rendering is rejected by the entity diff and re-generated | The wrong text stands |
| Unknown data | `INSUFFICIENT_DATA` — the gap is named | Plausible-sounding text |
| Multi-hop questions | Decomposed into ordered hops with a dependency graph | One prompt, one shot |
| Memory | Opt-in, on disk, visible, deletable | Implicit, opaque |
| Code you did not write | Executed only in the fail-closed Docker sandbox | Executed at the model's discretion |
| Creative work | Mechanical recombination, explicitly labelled `UNVALIDATED_COMBINATION` | Presented as an idea |
| Cost | No model API costs; search backends may become paid | Usually paid per token |
| Failure mode | Fails closed and shows the work | Fails silently and sounds confident |

**The AI Way wins at nuance, creativity and conversation.** II is not trying to
replace it; II is trying to be *checkable*.

---

## Architecture

```text
                      user request
                           │
                 ┌─────────▼─────────┐
                 │  core/ii_agent.py │  router: crisis → fast path →
                 │   route_task()    │  memory → interviewer → intent → DAG
                 └─────────┬─────────┘
                           │
        ┌──────────────────┼───────────────────────┐
        │                  │                       │
  crisis scan      fast path (simple          DAG (core/dag_planner.py)
  (empathy)        factual question)           asyncio, parallel branches
        │                  │                       │
        │                  │        ┌──────────────┼───────────────┐
        │                  │        │              │               │
        │                  │  logic tree     exocortex recall   interviewer
        │                  │  (hop by hop)   (opt-in memory)  (≤2 questions)
        │                  │        │              │               │
        │                  └────────┴──────────────┴───────────────┘
        │                                  │
        │                       deterministic JSON result
        │                                  │
        │                  ┌───────────────▼────────────────┐
        │                  │ core/caged_renderer.py          │
        │                  │ strict prompt + entity-diff     │
        │                  │ detector; reject → retry → dump  │
        │                  └───────────────┬────────────────┘
        ▼                                  ▼
   crisis resources              natural-language answer
   (nothing else runs)           with citations
```

### Package layout (Phase 0 consolidation)

| Path | Contents |
|---|---|
| `main.py`, `app.py`, `bootstrap.py` | The only root-level Python files. |
| `core/` | Router (`ii_agent.py`), state, security, model adapter, DAG planner, critic loop, caged renderer, NLP helpers. |
| `reasoning/` | Logic-tree solver, Z3 adapter, constraint compiler, verification cascade, bounded synthesis, TF-IDF utilities. |
| `ingestion/` | Web search/fetch (`ii_web.py`), repository ingestion, license screening. |
| `interaction/` | Interviewer, empathy module, brainstorming engine. |
| `invention/` | Combinatorial hypothesis engine (experimental). |
| `memory/` | Exocortex — disk-backed paged memory. |
| `server/` | Flask app helpers, UI heuristics. |
| `tools/` | Self-tool builder and friends. |
| `graph/`, `blackboard/`, `vendor/` | Repository graph, blackboard, remaining vendor adapters. |
| `archive/` | Quarantined scaffolding (see `archive/README.md`). |
| `self_tools/` | Generated tools + `tool_registry.json` (runtime, git-ignored). |

---

## The Exocortex (disk-backed paged memory)

The Exocortex is II's memory. It is **disk-backed and paged**, and it is
transparent by construction.

**What is stored**

| Table | Contents |
|---|---|
| `facts` | key, value, source URL, timestamp — only facts you approved |
| `episodes` | summary and timestamps of recorded work sessions |
| `tool_runs` | tool name, success flag, latency (bookkeeping only) |
| `meta` | consent state and index metadata |

Retrieval uses **TF-IDF keyword vectors** in an on-disk FAISS index — no
neural embedding model is downloaded, so the memory stays inside the RAM
budget. (A neural re-ranker is listed as future work with an explicit size
budget in `memory/exocortex.py`.)

**Profiling is opt-in — never silent**

* On first use II *asks* before storing anything.
* Nothing is written until you approve the memory feature **and** each
  individual proposed item.
* Silent background profiling is forbidden by design.
* Revoking consent deletes the stored facts.

```bash
# see exactly what is remembered
python main.py memory --action show

# approve the memory feature
python main.py memory --action optin

# forget one fact
python main.py memory --action forget --key "preference:language"

# forget everything and revoke consent
python main.py memory --action optout
```

The same view is available at `GET /api/memory` and `POST /api/memory/forget`.

---

## Install and run

**Requirements**: Python 3.11+. A local model is *optional* — without one II
renders structured results deterministically instead of through the LLM.

### macOS / Linux

```bash
./setup.sh          # creates .venv and installs requirements.txt
./start.sh          # starts the server on http://127.0.0.1:5000
./stop.sh           # stops it
```

### Windows

```bat
INSTALL_AND_RUN.bat
```

(the `.bat` launchers are unchanged; `setup.sh`, `start.sh` and `stop.sh`
mirror them.)

### Manual

```bash
python3.11 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python app.py                 # 127.0.0.1:5000
python app.py --host 0.0.0.0  # prints a loud exposure warning
```

Model weights are **not** bundled and are never downloaded automatically. For
local GGUF chat, place a compatible file in `models/` and install
`llama-cpp-python`, or start LM Studio at `http://127.0.0.1:1234`.

### Environment switches

| Variable | Meaning |
|---|---|
| `II_API_KEY` | When set, every `/api/*` route requires this key (`X-II-API-Key` header or `Authorization: Bearer`). Unauthenticated requests get **HTTP 401**. |
| `II_PRIVATE=1` | Disable every network-egress tool (search, fetch, research, repo ingestion). |
| `II_CODE_SANDBOX=docker` | Run generated code in the fail-closed Docker sandbox. `subprocess` (the default) is **not** a sandbox. |
| `II_REQUIRE_SANDBOX=1` | Refuse to execute code in subprocess mode at all. |
| `II_DATA_DIR` | Where the Exocortex stores its database and vector index. |

The server binds to **127.0.0.1 by default**. `--host` with any other address
prints a loud warning, because the API is a local research tool.

---

## Command line

```bash
python main.py ask "What is the capital of France?"   # full router pipeline
python main.py solve "Who is the CEO of the company that created WhatsApp?"
python main.py interview "Build a post, then publish it on my site"
python main.py brainstorm "urban food and energy"
python main.py hypothesize "reduce fouling on heat exchangers"
python main.py memory --action show
python main.py topology            # 'spectral' is a deprecated alias
python main.py verify --input path/to/file.py
python main.py graph
python main.py ingest --repo https://github.com/OWNER/REPO
```

---

## HTTP API

The server binds to `127.0.0.1:5000` by default.

| Method and path | Purpose |
|---|---|
| `GET /` | Chat page |
| `GET /api/status` | Backend state, **active pipeline stages** (reported truthfully), auth mode |
| `POST /api/chat` | The Pure II pipeline. Body: `{"message": "..."}`. Optional `"pipeline": "legacy"` for the older tool-calling loop. |
| `POST /api/solve` | Logic-tree solver; returns `{status, steps, final_answer, sources, missing}` |
| `POST /api/interview` | Constraint extraction + at most two questions |
| `GET /api/memory` | Exactly what the Exocortex remembers |
| `POST /api/memory/optin` | Grant or revoke memory consent |
| `POST /api/memory/forget` | Forget one key or everything |
| `POST /api/brainstorm` | Mechanical recombination of live components |
| `POST /api/hypothesize` | Sandbox-tested toy hypotheses (`SIMULATED`, never `VERIFIED`) |
| `GET /api/graph`, `GET /api/graph/topology` | Approximate repository index and topology |
| `POST /api/verify` | Static checks / fail-closed cascade |
| `POST /api/search`, `POST /api/fetch`, `POST /api/research` | Web tools (403 under `II_PRIVATE=1`) |
| `POST /api/repos/ingest`, `GET /api/repos/graph` | License-gated GitHub ingestion |
| `GET/POST /api/steer` | **501** — activation steering was archived |
| `POST /api/vision` | **501** — the vision prototype is not part of Pure II |

See [`docs/API.md`](docs/API.md) for the full contract and
[`docs/SECURITY.md`](docs/SECURITY.md) before exposing anything.

---

## Tests and benchmarks

```bash
python -m compileall -q -f .
python -m pytest tests -q                 # the whole suite
python -m pytest tests/benchmarks -q      # the benchmark suite
python -m pytest archive/tests -q         # the archived scaffolding tests
```

The benchmark suite (`tests/benchmarks/`) uses **external ground truth only** —
it never asserts that II agrees with itself:

| Benchmark | What it proves |
|---|---|
| `test_ground_truth.py` | 20 hand-written public facts: `VERIFIED`, expected answer present, ≥ 2 URLs, every URL HTTP 200, expected source domains |
| `test_logic_tree.py` | Canonical multi-hop questions produce the exact hop count and order |
| `test_citations.py` | Every URL resolves and every quote really appears on the page |
| `test_status_honesty.py` | The hypothesis pipeline never says `VERIFIED`; `SIMULATED` always carries a baseline and assumptions |
| `test_determinism.py` | The same query twice produces the same structure |
| `test_fail_closed.py` | A nonsense query returns `INSUFFICIENT_DATA` and fabricates nothing |
| `test_dag_concurrency.py` | Independent DAG nodes overlap in the event log (no wall-clock assertions) |
| `test_hallucination_detector.py` | Injected entities are caught; the blind spots are documented |
| `test_interviewer.py` | Never more than two questions, never an infinite loop |
| `test_exocortex_optin.py` | Zero memory writes before consent |
| `test_self_tool_builder.py` | Strict template, sandbox refusal, critic checks |
| `test_empathy.py` | Crisis-first behaviour, sourced techniques, mandatory disclaimer |
| `test_banned_claims.py` | The repository contains no banned claim |
| `test_ram_budget.py` | A complex task stays under 2 GB of resident memory |

Network-dependent benchmarks are **skipped** (not failed) when the machine has
no outbound internet, so the suite stays green offline while staying strict
online.

---

## Security

* **`II_API_KEY`** — set it and every `/api/*` route answers **401** without
  the key. Without it the API is unauthenticated, which is only acceptable
  because the server binds to localhost.
* **SSRF guard** — `/api/fetch` and every fetch inside the pipeline only
  accept public http(s) URLs. Loopback, private, link-local, reserved and
  cloud-metadata targets are refused.
* **`II_PRIVATE=1`** — disables every network-egress tool.
* **Code execution** — the default subprocess mode runs with your permissions
  and is **not** a sandbox. `II_CODE_SANDBOX=docker` is fail-closed Docker
  isolation; `II_REQUIRE_SANDBOX=1` refuses the subprocess mode entirely.
  **Fetched or generated code is only ever run in the Docker sandbox** — the
  self-tool builder fails closed when Docker is missing.
* **No silent profiling** — see the Exocortex section above.

Read [`docs/SECURITY.md`](docs/SECURITY.md) before exposing the server.

---

## Limitations, sources and licensing

* Web search and URL fetching contact external services. The system is not
  universally free, private or air-gapped.
* Repository graphs, clusters and PageRank are **navigation aids**, not
  architectural truth.
* The verification cascade fails closed unless caller-supplied test and
  entailment adapters are provided.
* Simulated hypotheses are toy-model estimates, never empirical proof.
* Model weights remain under their own licence. This project's code is MIT
  (see [`LICENSE`](LICENSE)); see [`docs/VENDOR_CREDITS.md`](docs/VENDOR_CREDITS.md).

## Project plan

* [`PLAN.md`](PLAN.md) — the phased implementation plan (v4 order).
* [`ROADMAP.md`](ROADMAP.md) — what is next, and what is explicitly out of
  scope.
