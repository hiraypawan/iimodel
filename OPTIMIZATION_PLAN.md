# II v2.0 — Optimization plan and status (honest tracker)

This file tracks optimizations with their **actual** state. Nothing is marked
done unless it is implemented and exercised by the test suite. Historical
"AGGRESSIVE MODE / beat Astra-Fable" framing has been removed: II is a
tool-augmented prototype around an optional local 1.5B model, and no
comparative benchmark against any other system has been run.

## Implemented

- [x] Identity pinned to II v2.0 in the system prompt
- [x] Search-synthesis fallback: when the model refuses or no backend is
  available, answers are built directly from search results (agentic mode)
- [x] Smart search query (`_smart_search_query`): abbreviation expansion and
  keyword fallback
- [x] Search cache in `ii_web.search_web` (300 s TTL, in-memory)
- [x] Message window trimmed before model calls (`messages[-6:]` in the chat
  loop) and `max_tokens=1024` on the main chat call
- [x] System prompt kept short; duplicate system prompts removed
- [x] `II_PRIVATE=1` no-network mode (web tools, search/fetch/research
  routes, and ingestion disabled; see `docs/SECURITY.md`)
- [x] SSRF guard (`ii_security.validate_public_url`) on URL fetching
- [x] Optional fail-closed Docker sandbox for `execute_code`
  (`II_CODE_SANDBOX=docker`); default subprocess mode labelled not-a-sandbox
- [x] Lean core dependencies (`requirements-core.txt`); heavy extras moved
  to `requirements-optional.txt`

## Not implemented (previously listed as ideas; do not treat as features)

- [ ] Parallel dispatch of independent tools in the chat loop
- [ ] Streaming model responses to the browser
- [ ] Response pre-fill from search snippets while the model generates
- [ ] Hard memory cap verified by measurement (`n_ctx`/GPU-layer settings
  exist as configuration, but no peak-RAM measurement has been published)
- [ ] Benchmark dataset and raw-model-vs-II comparison (dataset is empty)
- [ ] Hidden-state/logit/KV backend adapter needed for steering probes,
  entropy-MCTS, constrained decoding, and KV paging (Stages 2–4)
- [ ] UI renderer / promotion of any catalog component candidate
- [ ] Direct-GGUF latency comparison vs LM Studio (never measured)

## Notes on the search stack

Implemented priority in `ii_web.search_web`: Brave → SearXNG → DuckDuckGo
(`site:github.com` added for repo queries) → GitHub API → Playwright headless
(optional dependency) → Wikipedia → Crossref (academic queries only). Tavily
was mentioned in older comments but was never implemented or required an API
key; references to it have been removed.
