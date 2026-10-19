# II v3.0 — Pure Internet Intelligence (Hybrid Assistant + Tools)

**II = Internet Intelligence.** II's tool engine combines live internet data,
Python algorithms, graph traversal, Z3 solving, and DAG orchestration. Those
project tools remain the authority for research and computed work; no GGUF
weights or LM Studio installation is required.

For unrestricted conversation and dynamic tool selection, II supports
**ephemeral BYOK with OpenAI-compatible chat-completions providers**. The hosted
model interprets and renders; tool-capable models can call II to search, fetch,
solve, synthesize, verify, and inspect. Text-only compatible models still work
for ordinary conversation, while factual/tool-dependent requests fail closed.
Provider credentials are supplied in the UI for the current browser tab or through
environment variables, and are never written to the repository or database.

---

## Hybrid chat + the II tool engine

1. **Conversation and planning** — the user's OpenAI-compatible BYOK model
   handles general language and chooses a project tool when needed.
2. **Retrieval and execution** — live sources, graph algorithms, constraint
   solvers, and bounded tools perform the actual work.
3. **Tool-grounded rendering** — web-derived claims must use the evidence and
   citations returned by II. Failed retrieval must be reported, not filled in.
4. **Deterministic fallback** — without BYOK, the existing narrow router remains
   available for greetings and supported research/solver pipelines.

Generated or simulated code still follows the project's verification and
sandbox rules.

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
| `SUPPORTIVE_INFORMATION` | A conversational or supportive response; no factual web claim is being marked verified. |
| `CRISIS_RESOURCES` | Immediate crisis-support information; no other pipeline runs. |

**Fail-closed wins.** The system never returns an empty answer: it returns
`PARTIAL` (steps taken + sources found) and states exactly what is missing.
"No blank page paralysis" means *show your work*, not *invent an answer*.

---

## Honest capability statement

II's deterministic tools are designed to complement hosted language models
with:

* **live, cited research** — retrieved claims retain their source URLs,
* **fail-closed factual paths** — deterministic research abstains when it lacks
  evidence instead of filling gaps,
* **local repository analysis** — indexing, topology, and verification run on
  your machine,
* **user-controlled memory** — the Exocortex stores only what you approve,
* **debuggability** — every tool stage is a Python function you can inspect.

Privacy and cost depend on your chosen search and BYOK providers. In hybrid
mode, the provider receives conversation text and any tool output returned to
it. Without BYOK, II's deterministic fallback does **not** match a capable
language model at:

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

## Hybrid II vs hosted-model-only chat

| | Hybrid II | Hosted-model-only chat |
|---|---|---|
| Conversation | User-selected OpenAI-compatible model | Hosted model |
| Current facts | Model calls II search/research tools | Depends on provider features |
| Citations | II preserves URLs returned by retrieval | Optional, often absent |
| Tool failure | Exposed in `tool_trace`; an all-failed run discards the model's attempted factual answer | Provider-dependent |
| Deterministic work | Project graph, Z3, bounded synthesis, and verification tools | Provider-dependent |
| Memory | Opt-in, on disk, visible, deletable | Provider-dependent |
| Code execution | Not exposed to the hosted model's safe tool allowlist | Provider-dependent |
| Cost and privacy | Depend on the chosen BYOK and search providers | Depend on the provider |

The hosted model supplies the language fluency that deterministic II lacks; II
adds inspectable project tools and conservative failure handling.

---

## Architecture

```text
                           user request
                                │
                         app.py /api/chat
                                │
                ┌───────────────┴────────────────┐
                │ BYOK configured?              │ no
               yes                               ▼
                │                    core/ii_agent.py
                ▼                    deterministic fallback
       core/hybrid_agent.py                    │
       bounded tool-call loop      crisis / fast paths / intent / DAG
                │                               │
     ┌──────────┴──────────┐         search / graph / solvers / memory
     │                     │                    │
OpenAI-compatible     safe II tools             ▼
hosted model       search / research /     deterministic renderer
conversation +     graph / Z3 / synthesis       │
planning           / static verification        │
     └──────────┬─────────┘                      │
                ▼                                ▼
       tool-aware response              narrow fallback response
       + sources + trace                + status + sources
```

### Package layout (Phase 0 consolidation)

| Path | Contents |
|---|---|
| `main.py`, `app.py`, `bootstrap.py` | The only root-level Python files. |
| `core/` | Hybrid agent (`hybrid_agent.py`), deterministic fallback router, state, security, DAG planner, renderer, and NLP helpers. |
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

**Requirements**: Python 3.11+. Deterministic tools need no model weights. General chat requires a BYOK OpenAI-compatible endpoint.

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

II starts directly with its internet-and-tools engine. To enable general chat,
open **BYOK settings** in the web UI. Presets include OpenRouter, OpenCode
Zen/Go, Groq, Gemini, GitHub Models, Hugging Face, Cerebras, SambaNova, NVIDIA
NIM, Together, DeepInfra, Fireworks, Mistral, xAI, and OpenAI; custom
OpenAI-compatible endpoints remain supported. **Load models** asks the selected
provider's `/models` endpoint so model IDs do not need to be hardcoded. Every
built-in preset requires that provider's API key; a model labelled `free` is
free-tier, not anonymous. In particular, signing in to the OpenCode website is
not a substitute for creating and pasting an OpenCode Zen API key. Only a
custom endpoint that explicitly supports anonymous requests can omit the key.
The browser keeps configuration in tab-scoped session storage only.

II does not bundle provider credits or promise that a model is free. Free tiers,
quotas, tool-calling support, and model availability are controlled by each
provider and may change. For OpenRouter, **Load models** first requests the
authenticated account-filtered catalog and explicitly falls back to the public
catalog only when that credential cannot read the personalized route. Catalog
membership is still not proof that every API client can invoke a model. Use
**Test model** to make one confirmed, small inference request before saving; the
test can consume quota or credits and may make one text-only retry when tool
fields are unsupported.

OpenRouter can mark some models as available only to separately approved
agentic harnesses. II sends truthful app attribution and does not impersonate an
approved app. When an actual request returns a model-access restriction, II
removes that exact model from the current tab's choices for one hour and asks
for another model instead of repeatedly retrying or calling it an invalid key.
Text-only compatible models can handle ordinary chat; factual work requires a
model with OpenAI-style tool calling.

On hosted previews where server egress is restricted, enable **Connect to the
provider directly from this browser** (it defaults on for Arena's `.e2b.app`
preview). The browser talks to CORS-enabled providers while safe II tool calls
still execute through the authenticated II API. If browser CORS is unavailable,
II falls back to the normal server-side provider transport.

Alternatively, configure the server environment variables below.

### Environment switches

| Variable | Meaning |
|---|---|
| `II_API_KEY` | When set, every `/api/*` route requires this key (`X-II-API-Key` header or `Authorization: Bearer`). Unauthenticated requests get **HTTP 401**. |
| `II_PRIVATE=1` | Disable every network-egress tool (search, fetch, research, repo ingestion). |
| `II_CODE_SANDBOX=docker` | Run generated code in the fail-closed Docker sandbox. `subprocess` (the default) is **not** a sandbox. |
| `II_REQUIRE_SANDBOX=1` | Refuse to execute code in subprocess mode at all. |
| `II_DATA_DIR` | Where the Exocortex stores its database and vector index. |
| `II_WEB_TIMEOUT` | Per-provider search/fetch deadline in seconds (default `4`, bounded to `1..15`). |
| `II_CHAT_API_URL` | Server-side OpenAI-compatible base URL or full `/chat/completions` URL. |
| `II_CHAT_MODEL` | Provider model name used for hybrid chat. |
| `II_CHAT_API_KEY` | Provider key; optional for no-auth endpoints. Never returned by the API. |
| `II_CHAT_TIMEOUT` | Hosted-provider timeout in seconds (default `60`, bounded to `5..180`). |
| `II_CHAT_ALLOW_PRIVATE_ENDPOINT=1` | Explicitly allow a private/local BYOK endpoint; off by default to prevent SSRF. |

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
* This project's code is MIT (see [`LICENSE`](LICENSE)); third-party libraries
  retain their own licences (see [`docs/VENDOR_CREDITS.md`](docs/VENDOR_CREDITS.md)).

## Project plan

* [`PLAN.md`](PLAN.md) — the phased implementation plan (v4 order).
* [`ROADMAP.md`](ROADMAP.md) — what is next, and what is explicitly out of
  scope.
