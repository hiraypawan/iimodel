# Historical local/BYOK engine architecture

> This describes the retained Python tool engine, not the v0.3 decentralized
> worker network. See [`NETWORK-ARCHITECTURE.md`](./NETWORK-ARCHITECTURE.md)
> for host identities, signed releases, encrypted jobs, and worker execution.

II combines a local tool engine with an optional OpenAI-compatible BYOK
conversation/planning layer. Internet retrieval, Python algorithms, graphs,
solvers, and tools perform grounded work; no bundled GGUF model or LM Studio
installation is required.

## Request path (hybrid BYOK)

1. `POST /api/chat` accepts an ephemeral provider URL, model and optional key,
   or reads the corresponding `II_CHAT_*` environment variables. UI presets
   (including OpenRouter and OpenCode Zen/Go) only fill known base URLs; custom
   compatible endpoints remain first-class.
2. `POST /api/providers/models` can query a provider catalog. The browser uses
   OpenRouter's authenticated `/models/user` catalog first, labels fallback to
   its public catalog, and hides exact models that returned an access-policy
   rejection in this tab for one hour. A user-confirmed **Test model** action
   performs a bounded real inference probe before save. Model IDs, free tiers,
   pricing, policies, and quotas remain provider-controlled.
3. Anchored greetings, thanks, identity/help prompts, wellbeing checks, and
   farewells take the deterministic chat fast path before any provider request.
   This avoids latency and preserves hosted quota even when BYOK is configured.
   Otherwise `core/hybrid_agent.py` sends bounded conversation history and safe
   II tool schemas to the provider. On egress-restricted hosted previews, the
   same loop can run in the browser; `/api/hybrid/tools` exposes safe schemas and
   `/api/hybrid/tool` executes only that allowlist. Keys are never persisted or
   echoed.
4. The provider may answer ordinary conversation directly or call a bounded
   allowlist: search, fetch, research, SMT, bounded synthesis, static Python
   verification, and read-only blackboard inspection. Arbitrary code execution
   is not exposed. If an otherwise compatible endpoint rejects native tool
   fields, II retries text-only for ordinary chat and refuses factual/tool work.
5. Tool output is returned to the provider for its final response. Tool use is
   recorded in `tool_trace`; a tool-assisted answer is conservatively labelled
   `PARTIAL`, never automatically `VERIFIED`. If every requested tool fails,
   II discards any attempted provider answer and returns `INSUFFICIENT_DATA`.

## Request path (deterministic fallback)

1. `core/ii_agent.route_task(user_prompt)` receives the request.
2. **Crisis scan** (`interaction/ii_empathy_module.py`) runs first. A hit
   returns crisis resources and nothing else runs.
3. **Fast paths**: greetings, thanks, identity, and capability questions are
   answered locally with no network call. A short, single-step factual question
   (`core/ii_agent.is_simple_factual`) goes straight to the logic-tree solver.
   Neither fast path writes memory or invokes the interviewer.
4. **Memory context** is read from the Exocortex *only* when the user opted
   in; otherwise the context is empty.
5. **Interviewer** (`interaction/interviewer.py`) extracts slot constraints.
   A multi-step task with fewer than two critical slots produces at most two
   questions and then proceeds with documented defaults.
6. **Intent classification** is deterministic (word-boundary rules). Below
   0.5 confidence the default intent is `logic_tree` — *research it, don't
   chat-guess* — unless the request matches a tool-task pattern, in which case
   the self-tool builder takes over.
7. **DAG execution** (`core/dag_planner.py`): independent nodes run
   concurrently under asyncio, dependents are gated by the topological order,
   and every node start/end is written to an event log.
8. **Deterministic rendering** (`core/caged_renderer.py`): Python formats the
   resulting JSON into readable text without introducing claims beyond the
   structured tool result.

The older model-driven tool-calling loop is still reachable with
`{"pipeline": "legacy"}` and lives in `core/ii_agent.py` behind the
`run_agent` entry point.

## Module map

| Path | Responsibility |
|---|---|
| `core/hybrid_agent.py` | Ephemeral OpenAI-compatible BYOK client and bounded tool loop |
| `core/ii_agent.py` | Deterministic fallback router and tool definitions |
| `core/dag_planner.py` | Task graphs, parallel execution, event log |
| `core/critique_loop` → `core/critic_loop.py` | Sandbox run, Bandit scan, constraint checklist, bounded repair |
| `core/caged_renderer.py` | Deterministic structured-result renderer; legacy cage retained |
| `core/nlp.py` | spaCy helpers with a deterministic regex fallback |
| `core/ii_security.py` | `II_PRIVATE`, SSRF guard, code-execution modes |
| `core/ii_state.py` | Local decision/checkpoint bookkeeping |
| `core/llama_local.py` | Legacy optional renderer adapter (not used by Pure II) |
| `reasoning/logic_tree_solver.py` | Multi-hop decomposition and cited extraction |
| `reasoning/tfidf.py` | Dependency-free TF-IDF utilities |
| `reasoning/constraint_compiler.py` | Restricted explicit-formula parser |
| `reasoning/verification_cascade.py` | Fail-closed verification gates |
| `reasoning/ii_smt.py`, `reasoning/ii_synthesis.py` | Optional Z3 and bounded synthesis |
| `ingestion/ii_web.py` | Multi-backend search, SSRF-guarded fetch, evidence passages |
| `ingestion/repo_ingestor.py` | License-gated GitHub ingestion (parsed, never executed) |
| `interaction/interviewer.py` | Slot patterns, constraint tree, ≤ 2 questions |
| `interaction/ii_empathy_module.py` | Crisis-first support, VADER buckets, sourced techniques |
| `interaction/ii_brainstorming_engine.py` | SCAMPER recombination of live components |
| `invention/combinatorial_hypothesis_engine.py` | Sandbox-tested toy hypotheses (`SIMULATED`) |
| `memory/exocortex.py` | Disk-backed paged memory, opt-in profiling, FAISS keyword index |
| `tools/self_tool_builder.py` | Template-constrained tool generation, Docker-only execution |
| `graph/ii_graph.py`, `graph/topology.py` | Repository index; SCC/PageRank/clusters |
| `blackboard/ii_blackboard.py` | Experimental interface store and component catalog |
| `server/ii_ui_heuristics.py` | Optional Playwright UI heuristics |
| `archive/` | Quarantined scaffolding — see `archive/README.md` |

## Repository analysis

`graph/ii_graph.py` uses the Python AST and optional tree-sitter JS/TS
grammars. It records file/symbol definitions, some local imports and
unambiguous call references; dynamic behaviour and unresolved edges are
omitted. `graph/topology.py` provides SCCs, personalised PageRank and file
clusters as a **navigation aid, not an architecture proof**.
`graph/spectral_analysis.py` is a deprecated alias that prints a one-line
warning; the analysis is not eigenvalue-based spectral graph theory.

## Evidence handling

`ingestion/ii_web.py` keeps the exact extracted passage and the source URL
for every claim, and can emit heuristic conflict signals. Those signals are
review prompts, **not** fact-checking: the logic-tree solver decides what is
`VERIFIED` by requiring two independent source domains for the final hop.

## Memory

The Exocortex stores facts, episodes and tool-run bookkeeping in SQLite, and
indexes the facts with TF-IDF keyword vectors in an on-disk FAISS index. It
never writes anything before the user opts in, and it can show and delete
everything it holds.

## Archived experiments

Activation steering, linear probes, TIES/DARE array maths, KV-cache paging,
MobileCLIP vision and entropy-MCTS were moved to `archive/`. They are not
imported by the runtime; see `archive/README.md` for why each one was
quarantined.

## Engine boundaries

II may retrieve source text and run explicit algorithms and tools. The
fallback renderer may not fill evidence gaps with invented facts. Hybrid
responses are provider-generated and therefore are never labelled `VERIFIED`;
the system prompt requires tool use for current/factual claims, and an
all-failed tool run is replaced with `INSUFFICIENT_DATA`. Simulations remain
explicitly unverified. Code execution is not in the hybrid model's allowlist
and all other execution remains subject to the documented sandbox policy.
