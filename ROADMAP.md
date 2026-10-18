# II v3.0 — Roadmap

This replaces the old `OPTIMIZATION_PLAN.md`. It records what is **done**,
what is **next**, and what is **deliberately not planned** — with the same
honesty rules as the rest of the project: nothing is marked done unless it is
implemented *and* exercised by the test suite, and no future item is
described as if it already worked.

---

## Done (v3.0, "Pure II")

- [x] **Repo consolidation** — package directories own the code; only
      `main.py`, `app.py` and `bootstrap.py` remain at the root.
- [x] **Scaffolding quarantine** — activation steering, linear probes,
      TIES/DARE maths, KV paging, MobileCLIP and entropy-MCTS moved to
      `archive/` with a README explaining each removal.
- [x] **Logic-tree solver** — multi-hop decomposition, topological execution,
      cited extraction, fail-closed statuses.
- [x] **Exocortex** — disk-backed paged memory, opt-in profiling, visible and
      deletable.
- [x] **DAG planner** — parallel independent nodes, event log, fast path.
- [x] **Interviewer** — explicit slot patterns, at most two questions, then
      defaults.
- [x] **Empathy module** — crisis-first, sourced techniques, mandatory
      disclaimer.
- [x] **Brainstorming engine** — mechanical SCAMPER recombination, two-tier
      citations, `UNVALIDATED_COMBINATION` labelling.
- [x] **Self-tool builder + critic loop** — strict template, Docker-only
      execution, exact-traceback repair, Bandit scan, constraint checklist.
- [x] **Hypothesis engine** — sandbox-tested toy simulations, `SIMULATED`
      only, mandatory baseline and assumptions.
- [x] **Router** — deterministic intent classification, crisis and fast
      paths, DAG execution.
- [x] **Caged renderer + hallucination detector** — strict prompt, entity
      diff, reject → retry → raw-JSON fallback, logged rejections.
- [x] **Benchmarks** — external ground truth, honesty, determinism,
      fail-closed, concurrency, detector, interviewer, opt-in, banned claims,
      RAM budget.
- [x] **Safety** — `II_API_KEY` (401), `II_PRIVATE`, SSRF guard, Docker
      sandbox mode, `--host` exposure warning, cross-platform launchers.

---

## Next (ordered)

1. **Live-source quality in the benchmarks.** The ground-truth set runs
   against free search backends whose ranking changes. The next step is a
   reviewed, versioned question set with per-question source allow-lists and
   a periodic re-run job that reports *drift* instead of failing on it.
2. **Conflict handling.** `CONFLICT` is detected for numeric disagreements.
   Extending it to attribute-level conflicts (two sources giving different
   founders, different dates) needs a typed comparison per slot.
3. **Neural re-ranking in the Exocortex** — explicitly *not* done in v3.0.
   The budget is ~45 MB RAM for an int8 MiniLM; it must ship with an offline
   fallback and a visible size report.
4. **Constraint checklist depth.** The critic loop currently checks that
   specified slots appear in generated code. Slot-specific checks (a
   "platform = web" constraint that implies an HTML artefact) would be more
   useful.
5. **Self-tool registry hygiene** — signing, review status and a rollback
   path for generated tools.
6. **DAG re-planning** — when a node fails, the planner currently reports the
   failure. A bounded re-plan (retry the node with a different backend) would
   improve the `PARTIAL` rate without weakening the fail-closed rule.
7. **Measured memory profile on target hardware** — the RAM budget is
   asserted with `getrusage` in the test suite; a published measurement on
   the reference machine (model file, context length, backend) is still
   missing.

---

## Deliberately not planned

* **Media and 3D orchestration** (FFmpeg video generation, Blender, React or
  Node app scaffolding). Deferred to a future phase outside the 2 GB core.
* **Neural embeddings in the memory index** (see above — budgeted, not
  planned for this release).
* **Model training, fine-tuning or weight modification.** II never trains a
  model.
* **Steering, probes, KV-cache transfer, TIES/DARE merging.** Archived; they
  need an inference backend that exposes hidden states, logits or a forkable
  KV cache, which the shipped backends do not.
* **Vision grounding.** The archived prototype computes whole-image
  similarity only; real localisation is a different project.
* **Any capability score, hallucination percentage or efficiency claim.**
  These need a published measurement protocol; until then the project
  publishes none.
