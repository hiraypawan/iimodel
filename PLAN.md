# Historical local-engine implementation plan

> This document records the earlier Python/BYOK research engine under `core/`,
> `reasoning/`, and related packages. It is retained for traceability. The active
> decentralized worker/Jarvis product roadmap is [`ROADMAP.md`](./ROADMAP.md),
> and the current network truth is in [`README.md`](./README.md).

Each historical phase below lists what was built, what was deliberately **not**
built, and where that local-engine code lives.

The governing rules live in the README: the LLM is the renderer, the status
taxonomy is fixed, and no claim may outrun its evidence.

---

## Phase 0 — Repo surgery (done)

**Built**

* The duplicate root/package layout was consolidated: **package directories
  win**. Root-level Python files are now only `main.py`, `app.py` and
  `bootstrap.py`.
  * `ii_agent.py`, `ii_state.py`, `ii_security.py`, `llama_local.py` → `core/`
  * `ii_blackboard.py` → `blackboard/`
  * `ii_graph.py` → `graph/`
  * `ii_smt.py`, `ii_constraints.py`, `ii_synthesis.py` → `reasoning/`
  * `ii_web.py` → `ingestion/` (with the historical `import ii_web` alias kept
    alive through `ingestion/__init__.py` and `tools/ii_web.py`)
  * `ii_ui_heuristics.py` → `server/`
  * `ii_vision.py` → `archive/`
* Dead scaffolding was quarantined in `archive/` with a README explaining
  every removal: activation steering, linear probes, TIES/DARE array maths,
  KV-cache paging, MobileCLIP, entropy-MCTS.
* `spectral` is now a **deprecated alias** for the canonical `topology`
  module; using it prints a one-line warning.
* API authentication: `II_API_KEY` from the environment; unauthenticated
  `/api/*` calls answer **401**. The server binds `127.0.0.1` by default and
  `--host` prints a loud warning when it exposes anything else.
* Cross-platform launchers `setup.sh`, `start.sh`, `stop.sh` mirror the
  untouched `.bat` files.

**Not built**: nothing was deleted that the runtime still needs; the archived
modules remain importable and their tests still run.

---

## Phase 1 — Logic-tree solver (`reasoning/logic_tree_solver.py`)

**Built**

* `HOP_PATTERNS` (leadership, creator, attribute, comparison) matched over the
  question, plus creator relations recovered from the spaCy dependency parse.
* Nested questions are split **innermost-first**: the outer entity is trimmed
  and the inner hop becomes its dependency.
* A `networkx.DiGraph` of the hops; the execution order is the topological
  order. Maximum 4 hops; unmatched questions become a single direct hop.
* Hops execute sequentially against live sources with a 20 s per-hop budget.
  A hop that cannot be resolved stops the run and the result becomes
  `PARTIAL` (or `INSUFFICIENT_DATA` when nothing resolved).
* Extraction: trafilatura → spaCy sentences → TF-IDF ranking against the hop
  query → top 3 passages with their URLs.
* Output: `{status, steps, final_answer, sources, conflicts, missing, ...}`.
  **No LLM participates in this pipeline.**

**Not built**: no semantic web reasoning, no query planner that invents
sub-questions, no claim verification beyond passage ranking.

---

## Phase 2 — Exocortex (`memory/exocortex.py`)

**Built**

* SQLite tables `facts`, `episodes`, `tool_runs`, `meta` on disk.
* An on-disk **FAISS** index over TF-IDF keyword vectors (with a JSON
  fallback when FAISS is unavailable). **No neural embedding model** is
  downloaded; a neural re-ranker is documented as future work with a size
  budget.
* **Profiling is opt-in**: consent is asked for, individual items are approved
  individually, and nothing is written before that. A `memory forget <key>`
  command and a visible "what I remember" view are provided.
* LRU cache for fetched pages; heavy libraries are imported lazily.

**Not built**: no silent profiling, no neural embeddings, no cloud sync, no
"infinite" memory of any kind.

---

## Phase 3 — DAG planner (`core/dag_planner.py`)

**Built**

* Tasks become a `networkx.DiGraph`; independent nodes run **concurrently**
  under asyncio; dependents are gated by the topological order.
* A fast path skips the interviewer and the memory write for single-hop
  factual questions.
* Every node start/end is written to an **event log** — the concurrency
  benchmark asserts on overlapping intervals, never on wall-clock speed.

**Not built**: no dynamic re-planning, no cost-based scheduling, no
distributed execution.

---

## Phase 4 — Interviewer (`interaction/interviewer.py`)

**Built**

* Constraint extraction by **explicit slot patterns** (regex + spaCy NER) for
  dates, money, audience, platform and tech stack.
* A multi-step task with fewer than two critical slots filled produces **at
  most two** targeted questions, after which the pipeline proceeds with
  documented defaults. It never loops and never blocks on trivia.
* The output is a `ConstraintTree` dict consumed by the DAG planner and the
  critic loop.

**Not built**: no dialogue manager, no follow-up question generator, no
learning of user preferences from the answers.

---

## Phase 5 — Empathy (`interaction/ii_empathy_module.py`)

**Built**

1. The crisis keyword scan runs **first**; on a hit, crisis resources are
   returned and no other module runs.
2. VADER sentiment bucketing into frustration / anxiety / sadness.
3. Curated, **sourced** frameworks: validation plus one or two grounding
   techniques, each with a source URL. **No efficacy percentages.**
4. The model formats the result; the mandatory closing disclaimer is appended
   by code so it can never be dropped.

**Not built**: no diagnosis, no therapy, no crisis outreach automation, no
sentiment model other than VADER.

---

## Phase 6 — Brainstorming (`interaction/ii_brainstorming_engine.py`)

**Built**

* Live components are fetched, then recombined with **SCAMPER operators
  written as explicit Python functions**. At most three outputs.
* Two-tier citations are mandatory: `component_evidence` (URLs proving each
  component exists) and `combination_status: "UNVALIDATED_COMBINATION"`.
* The model formats only; it may not add ideas.

**Not built**: this is **mechanical recombination, not invention**, and the
code, comments and docs say so everywhere.

---

## Phase 7 — Self-tool builder + critic loop (`tools/self_tool_builder.py`, `core/critic_loop.py`)

**Built**

* The builder is used by the `code` intent.  That intent is reached either
  because the deterministic classifier recognised a code request, or because
  the router's confidence was below 0.5 **and** the request matched a
  tool-task pattern (an unclassified "build me a tool ..." request).
* Reference code is fetched through the ingestion layer and then rewritten
  into a strict template: allowlisted imports, a mandatory `try`/`except`, an
  entry point, and network access only through the SSRF-guarded fetcher.
* **Fetched code is only ever executed in the fail-closed Docker sandbox.**
  Without Docker the run fails; there is no silent subprocess fallback.
* The repair loop feeds the **exact traceback** to the model with "Fix ONLY
  the failing lines. Return full code." — at most three attempts.
* Verified tools are saved to `self_tools/{category}/{tool_name}.py` and
  registered in `self_tools/tool_registry.json`; the agent loads the registry
  at startup as structured tool definitions.
* The critic loop runs: sandbox execution → Bandit security scan → checklist
  against the Interviewer's `ConstraintTree`, with the same bounded repair
  loop. The **accessibility checklist applies only to generated web-UI code**.

**Not built**: no package publishing, no auto-install of generated tools, no
marketplace, no self-modifying core.

---

## Phase 8 — Hypothesis engine (`invention/combinatorial_hypothesis_engine.py`)

**Built**

* `CombinatorialHypothesisEngine` fetches two live domains, builds
  template-based combinations (capped at 30), ranks them by
  `source_quality × plausibility × novelty`, and simulates the best ones.
* The status is **always** `SIMULATED` — never `VERIFIED`. Every result
  carries the baseline (simulated **first**), the delta, **non-empty**
  assumptions, the disclaimer *"Toy model estimate, not empirical proof."*
  and the component sources.
* The plausibility checklist must pass completely before anything is
  simulated: no conservation-law violation on paper, named materials must
  exist (quick web check), operating conditions must be stated.
* Simulation code is model-generated under §1.2(b) and sandbox-verified.

**Not built**: no empirical experiment, no physical validation, no claim that
any hypothesis is true.

---

## Phase 9 — Router (`core/ii_agent.py`)

**Built**

* `route_task(user_prompt)` in the fixed order: crisis scan → fast path for
  simple factual questions → memory context (opt-in only) → interviewer (≤ 2
  questions, then defaults) → deterministic intent classification → DAG
  execution → caged rendering.
* `classify_intent()` uses explicit word-boundary rules and returns a
  confidence; below 0.5 the default intent is `logic_tree` — *research it,
  don't chat-guess* — or the self-tool builder when the request is a tool
  task.
* `is_complex_logical_query()` is concrete: ≥ 2 hop indicators **or** ≥ 3
  named entities.

**Not built**: no model-based routing, no reinforcement of the classifier, no
multi-turn planning.

---

## Phase 10 — Caged renderer + detector (`core/caged_renderer.py`)

**Built**

* The strict system prompt, verbatim:
  *"YOU ARE A DETERMINISTIC TEXT RENDERER. Convert the provided JSON into
  natural, fluent Hinglish/English. ZERO HALLUCINATION: You MUST NOT add any
  facts, numbers, or names not explicitly present in the JSON. Stick strictly
  to the schema."*
* `check_llm_output(llm_text, source_json)` performs an entity diff with
  number normalisation (`15%` ≡ `15 percent`).
* On rejection the rendering is discarded, retried once, and finally replaced
  by a formatted dump of the raw JSON. **Every rejection is logged.**
* A **demo** backend (the bundled non-generative placeholder) is never used
  as a renderer: it returns canned text, which the detector would correctly
  reject.  Without a real generative backend the deterministic renderer
  formats the JSON instead.
* The known blind spots are documented in the code, not hidden: relation
  flips, negation/qualifier changes, paraphrase false positives.

**Not built**: no semantic entailment check, no claim-level verification.

---

## Phase 11 — Benchmarks (`tests/benchmarks/`)

Implemented as described in the README's benchmark table: external ground
truth only, no circular self-assertion. Network-dependent benchmarks skip
cleanly when the machine is offline.

---

## Phase 12 — Documentation

README rewritten for v3.0 (including "The II Way vs The AI Way"), this plan
rewritten in the v4 order, `OPTIMIZATION_PLAN.md` replaced by `ROADMAP.md`,
and `docs/API.md`, `docs/ARCHITECTURE.md`, `docs/SECURITY.md` updated.

---

## Explicitly out of scope

The "Universal Orchestrator" media ambitions — FFmpeg video generation,
Blender 3D, React/Node app scaffolding — are **deferred to a future phase
outside the 2 GB core**. They conflict with the RAM limit and explode the
scope. `pandas`/`numpy`/`matplotlib` may be used where already light, but no
new media or 3D subsystem is introduced.

## Definition of done

* `python -m compileall -q -f .` — clean.
* The full test suite is green after Phase 0.
* All Phase 11 benchmarks are green (network-dependent ones skip offline).
* A complex task (search + code-gen + render) stays under 2 GB of resident
  memory — checked with `resource.getrusage` in
  `tests/benchmarks/test_ram_budget.py`.
* `grep` for every banned claim returns zero hits — enforced by
  `tests/benchmarks/test_banned_claims.py`.
* `GET /api/status` reports the active pipeline stages truthfully.
