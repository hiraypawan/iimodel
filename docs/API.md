# II v3.0 — API reference

The server binds to `127.0.0.1:5000` by default.

## Authentication

Set `II_API_KEY` and every `/api/*` route requires it:

```
X-II-API-Key: <key>
```

or

```
Authorization: Bearer <key>
```

Unauthenticated requests are answered with **HTTP 401**:

```json
{
  "error": "unauthorized",
  "detail": "This API requires the II_API_KEY. Send it as the 'X-II-API-Key' header or as 'Authorization: Bearer <key>'."
}
```

Without `II_API_KEY` the API is unauthenticated — acceptable only on a
localhost-bound server. `GET /api/status` reports the active mode in
`api_auth`.

## Routes

| Method and path | Purpose | Notes |
|---|---|---|
| `GET /` | Chat page | Serves the local UI without embedding server credentials. When `II_API_KEY` is enabled, enter it in tab-scoped settings. |
| `GET /api/status` | Engine state and **active pipeline stages** | Reports `backend`, `engine_ready`, `hybrid_chat_configured`, `hybrid_chat_mode`, `hybrid_browser_direct`, truthful `pipeline_stages`, `private_mode`, `web_tools_enabled`, `code_sandbox`, and `api_auth`. Never returns provider credentials. |
| `POST /api/chat` | Hybrid BYOK or deterministic fallback | Body: `{"message":"...", "history":[...], "provider":{"api_url":"https://…/v1", "model":"…", "api_key":"…"}}`. When provider configuration is present, an OpenAI-compatible model handles conversation and dynamically calls safe II tools. Provider data is ephemeral. Without it, the narrow deterministic router runs. Response includes `pipeline`, `response_kind`, `tool_trace`, and `sources`; provider failures are JSON with HTTP **424**. `pipeline: "legacy"` remains for compatibility. |
| `POST /api/providers/models` | Discover provider models | Body: `{"provider":{"api_url":"https://…/v1", "api_key":"…", "models_url":"https://…/models"}}`; `models_url` is optional and restricted to the provider host or a trusted preset pair. Calls the catalog, returns at most 500 model IDs, and never returns the key. The UI prefers OpenRouter's authenticated `/models/user` catalog and explicitly labels public fallback. Provider failures use HTTP **424** with JSON so proxies do not replace details with an HTML 502 page. |
| `GET /api/hybrid/tools` | Browser-direct schemas | Returns the hybrid system prompt, safe tool schemas, and loop limits. Never includes `execute_code`. |
| `POST /api/hybrid/tool` | Execute one safe hybrid tool | Body: `{"name":"search_web", "arguments":{"query":"…"}}`. Returns `{ok, output, output_preview, sources}`. This is used by browser-direct BYOK transport and remains subject to `II_API_KEY` and `II_PRIVATE`. |
| `POST /api/solve` | Logic-tree solver | Body: `{"query": "..."}`. Returns `{status, steps, final_answer, sources, conflicts, missing, hop_count, execution_order}`. `status` is one of `VERIFIED`, `PARTIAL`, `CONFLICT`, `INSUFFICIENT_DATA`. |
| `POST /api/interview` | Constraint extraction | Body: `{"prompt": "..."}`. Returns `constraint_tree`, `questions` (≤ 2) and `proceed_with_defaults`. |
| `GET /api/memory` | What the Exocortex remembers | `description`, `facts`, `episodes`, `tool_runs`. Shows exactly what is stored. |
| `POST /api/memory/optin` | Grant or revoke memory consent | Body: `{"granted": true|false}`. Revoking deletes the stored facts. |
| `POST /api/memory/forget` | Forget stored facts | Body: `{"key": "name"}` forgets one key; omitting `key` forgets everything. |
| `POST /api/brainstorm` | Mechanical recombination | Body: `{"topic": "..."}`. Returns `outputs` (≤ 3), `component_evidence` and `combination_status: "UNVALIDATED_COMBINATION"`. |
| `POST /api/hypothesize` | Sandbox-tested toy hypotheses | Body: `{"problem": "..."}`. Returns `status` (`SIMULATED` or `INSUFFICIENT_DATA`), `hypothesis`, `baseline`, `delta`, `assumptions`, `disclaimer`, `sources`. **Never `VERIFIED`.** |
| `GET /api/graph` | Approximate repository index | A navigation aid over an incomplete call graph. |
| `GET /api/graph/topology` | Topology summary, SCCs, clusters | Approximate; not an architecture proof. |
| `POST /api/verify` | Static checks or the optional 3-gate cascade | Body: `{"code": "...", "mode": "static"\|"cascade"}`. The cascade fails closed when caller adapters are absent. |
| `GET/POST /api/steer` | Activation steering | **501** — the feature was archived in v3.0 and is not wired into the runtime. |
| `POST /api/vision` | Vision grounding | **501** — the prototype computes whole-image similarity only and is not part of Pure II. |
| `POST /api/search` | Web search (multi-backend) | External request. 403 under `II_PRIVATE=1`; 400 on an empty query. |
| `POST /api/fetch` | Fetch a URL's text | External request. SSRF-guarded: non-public URLs return 400; 403 under `II_PRIVATE=1`. |
| `POST /api/research` | Search → fetch → evidence → conflict signals | External requests; 403 under `II_PRIVATE=1`. |
| `POST /api/verify-citations` | Check that `[n]` markers map to real sources | Mechanical check only; it does not verify facts. |
| `GET /api/state` | Local decision/checkpoint snippets | State lives in the configured work directory. |
| `POST /api/smt` | Optional explicit Z3 constraints | 503 when Z3 is unavailable. Not a natural-language maths solver. |
| `POST /api/synthesize` | Bounded expression search | Toy examples only. |
| `POST /api/blackboard`, `GET /api/blackboard/graph` | Interface store and its approximate graph | Structural only; no component build pipeline. |
| `POST /api/repos/ingest` | Shallow-clone a public GitHub repo, screen its licence, cache a graph | Body: `{"url": "https://github.com/OWNER/REPO"}`. Blocking, 60 s clone timeout, 5 000-file cap. The source is parsed, never executed. 403 under `II_PRIVATE=1`. |
| `GET /api/repos/graph?repo=OWNER/REPO[&target=SYMBOL]` | Query a cached repository graph | Requires an existing cache. |

## Status vocabulary (exact labels)

`VERIFIED`, `PARTIAL`, `SIMULATED`, `UNVALIDATED_COMBINATION`, `CONFLICT`,
`INSUFFICIENT_DATA`, `SUPPORTIVE_INFORMATION`, `CRISIS_RESOURCES`.

`VERIFIED` is emitted only by deterministic evidence paths and requires at
least two **independent** source domains for the final claim. Hybrid tool runs
remain `PARTIAL` because provider-generated prose is not mechanically verified;
`tool_trace` and `sources` expose the work performed. If all hybrid tool calls
fail, the provider's attempted answer is discarded and the status is
`INSUFFICIENT_DATA`. `SIMULATED` always carries a baseline, non-empty
assumptions and the disclaimer *"Toy model estimate, not empirical proof."*

## Notes

* All network-egress endpoints are disabled when the server is started with
  `II_PRIVATE=1`.
* Code execution is not sandboxed unless `II_CODE_SANDBOX=docker` is set;
  `II_REQUIRE_SANDBOX=1` refuses the subprocess mode entirely. Fetched or
  generated code is Docker-only by construction.
* See [`SECURITY.md`](SECURITY.md) before exposing the server.
