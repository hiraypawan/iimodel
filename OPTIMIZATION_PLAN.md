# II Optimization Plan — vs Astra / Fable (AGGRESSIVE MODE)

## Done (current fixes)
- Identity fixed (not Anthropic)
- Anti-refusal + refusal bypass (search results direct when model refuses)
- Smart search query (`_smart_search_query`): expands abbreviations (`REA` → `reverse engineering`), adds `github`, falls back to shorter keywords
- Search priority: SearXNG (meta) > DuckDuckGo (`site:github.com` for repos) > GitHub API > Wikipedia > Crossref (skipped for non-academic)
- System prompt shortened (reduces LM Studio token load from ~5000 to ~2000)
- Message duplication removed (no duplicate system prompts in loop)
- Search fallback: shorter keywords if 0 results

## JUGAAD / TRICKS (immediate speed + quality)
1. **Direct synthesis bypass** (active): When Qwen refuses, agent builds answer directly from search results — no LM Studio bottleneck for web facts.
2. **Prompt compression**: System prompt uses abbreviations (`II` not full description), citation rules summarized to 5 lines, no redundant identity rules.
3. **Message trim**: Before MODEL.chat, trim messages to `system + last 4 turns` — prevents 8733-token prompts (20-60s delays).
4. **Search cache** (`_search_cache`): Cache results by query string for 300s — avoids re-searching.
5. **Parallel tool dispatch**: Execute `search_web` + `blackboard` + `execute_code` simultaneously where independent.
6. **Direct GGUF fast path**: When `llama-cpp-python` available and model file present, use `LocalModel` in `gguf` mode (bypass LM Studio network overhead entirely).
7. **Streaming response**: Return `final_text` as it arrives from LM Studio (stream=True) instead of blocking until full response.
8. **Response pre-fill**: Pre-fill `final_text` with search snippet summary before LM Studio finishes — user sees content immediately.
9. **Memory cap**: Force `n_ctx=2048`, `n_gpu_layers=0` for Qwen 1.5B to stay under 2GB RAM.
10. **Private mode toggle**: Config flag `II_PRIVATE=true` disables all external calls (`search_web`, `read_url`, `research`) — pure local inference + blackboard + code execution only.

## Best free search stack (applied)
- **SearXNG** (meta-aggregator: Bing/Google/Yandex/DDG) — best free coverage, no API key
- **DuckDuckGo** (`site:github.com` for repos) — direct HTML results
- **GitHub API** (`/search/repositories`) — direct repo data for code queries
- **Wikipedia** — authoritative facts
- **Crossref** — ONLY for clearly academic queries (`study`, `paper`, `doi`, `journal`)
- **Fallback**: shorter keywords (`first 3 words + github`) when 0 results

## To beat Astra / Fable (required)
Both Astra and Fable are orchestration agents with multi-model, sandbox, benchmark comparison, and full pipeline integration. II needs:

1. **Benchmark harness** (`tests/benchmarks/`): run raw Qwen vs II-pipeline side-by-side; measure accuracy, citation correctness, tool-call rate, time-to-answer.
2. **Sandbox isolation** (`execute_code` → Docker container with time limits, no user permissions, read-only filesystem, isolated network).
3. **Direct GGUF adapter** (`llama-cpp-python` installed): eliminate LM Studio bottleneck for faster inference (1-2s vs 20-60s).
4. **Hidden-state adapter** (`reasoning/entropy_mcts.py`): wire logit access for entropy-triggered search and steering probes.
5. **Full 4-stage pipeline**: pre-inference (graph) → middle-layer (probes/steering) → decoding (FSM/constrained) → post-generation (sandbox + entailment gates 2+3).
6. **UI renderer**: approve/promote 1 of 10 Jinja component candidates with build/review gates.
7. **Private/air-gapped mode**: disable all external tools via config; use only local GGUF + blackboard + code execution.
8. **Memory measurement**: measure peak RAM/VRAM with `qwen2.5-1.5b-instruct-q4_0.gguf` loaded directly.
9. **No false claims**: publish `docs/LIMITATIONS.md`: prototype only, no MMLU/HumanEval result, no 98.5% claim, no universal superiority.

## Next actions (priority order)
- [x] Smart search + refusal bypass + shorter prompt
- [x] Message deduplication + search fallback
- [ ] Add `_search_cache` (300s TTL dict) to `search_web`
- [ ] Trim `messages` to `system + last 4 turns` before MODEL.chat
- [ ] Set `max_tokens=1024` in `/api/chat` to reduce generation time
- [ ] Test direct GGUF with `.venv311` using installed `llama-cpp-python`
- [ ] Add benchmark dataset (10 review tasks) to `tests/benchmarks/`
- [ ] Measure memory: `python -c "import psutil; ..."` during inference
- [ ] Add Docker sandbox option to `execute_code`
- [ ] Add `II_PRIVATE=true` config flag
