# II v3.0 — Architecture

II is a local, deterministic reasoning system. The optional 1.5B model is a
**renderer**; every conclusion comes from Python algorithms over live data.

## Request path (Pure II)

1. `core/ii_agent.route_task(user_prompt)` receives the request.
2. **Crisis scan** (`interaction/ii_empathy_module.py`) runs first. A hit
   returns crisis resources and nothing else runs.
3. **Fast path**: a short, single-step factual question
   (`core/ii_agent.is_simple_factual`) goes straight to the logic-tree
   solver — no interviewer, no memory write.
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
8. **Caged rendering** (`core/caged_renderer.py`): the model formats the
   resulting JSON under the strict system prompt, and the entity-diff
   detector rejects (and finally replaces) any rendering that contains
   entities the JSON does not have.

The older model-driven tool-calling loop is still reachable with
`{"pipeline": "legacy"}` and lives in `core/ii_agent.py` behind the
`run_agent` entry point.

## Module map

| Path | Responsibility |
|---|---|
| `core/ii_agent.py` | Router, intent rules, legacy agent loop, tool definitions |
| `core/dag_planner.py` | Task graphs, parallel execution, event log |
| `core/critique_loop` → `core/critic_loop.py` | Sandbox run, Bandit scan, constraint checklist, bounded repair |
| `core/caged_renderer.py` | Strict renderer prompt + hallucination detector |
| `core/nlp.py` | spaCy helpers with a deterministic regex fallback |
| `core/ii_security.py` | `II_PRIVATE`, SSRF guard, code-execution modes |
| `core/ii_state.py` | Local decision/checkpoint bookkeeping |
| `core/llama_local.py` | GGUF / LM Studio / demo model backends |
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

## What the model may and may not do

May: format JSON into natural language; generate short code snippets from a
precise spec (always sandbox-verified before use).

May not: add facts, invent conclusions, brainstorm, opine, or decide what is
true. The detector catches entity injections; the documented blind spots are
relation flips, negation/qualifier changes and paraphrase false positives.
