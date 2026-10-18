# API reference (current Flask app)

The server binds to `127.0.0.1:5000` by default. These endpoints are local
prototype interfaces without authentication. Do not expose them to untrusted
clients.

| Method and path | Purpose | Notes |
|---|---|---|
| `GET /` | Chat page | Displays backend/demo status. |
| `GET /api/status` | Backend and optional-tool availability | `chat_ready` is false in demo mode. Also reports `private_mode`, `web_tools_enabled`, and `code_sandbox`. |
| `POST /api/chat` | Chat with the configured model | Tool use depends on model output. Web tools make outbound requests (disabled with 403-style private responses under `II_PRIVATE=1`); code execution is not isolated unless `II_CODE_SANDBOX=docker`. |
| `POST /api/build` | Generate a Python code draft | Uses static checks and model repair; does not run tests or prove correctness. |
| `GET /api/graph` | Approximate repository file/symbol index | Not semantic search or data-flow analysis. |
| `GET /api/graph/topology` | Approximate topology summary, SCCs, and file clusters | Results depend on the incomplete call graph. |
| `POST /api/verify` | Static checks or incomplete optional 3-gate cascade | JSON body: `{"code":"...", "mode":"static"}`. Cascade mode fails closed when caller adapters are absent; neither mode proves safety/correctness. |
| `GET /api/steer` | List proposed and locally available steering-vector names | No vectors are bundled; this does not apply steering. `POST` apply returns 501 because model-layer hooks are unavailable. |
| `POST /api/search` | Web search (Brave/SearXNG/DuckDuckGo/GitHub/Wikipedia) | External network request; results may be incomplete. Returns 403 under `II_PRIVATE=1`; 400 on an empty query. |
| `POST /api/fetch` | Fetch URL text and metadata | External request; source metadata is not a credibility guarantee. SSRF-guarded: non-public URLs return 400; all fetches return 403 under `II_PRIVATE=1`. |
| `GET /api/state` | Read local state snippets | State files are stored in the configured work directory. |
| `POST /api/smt` | Optional explicit Z3 constraints | Returns 503 when Z3 is unavailable. Not a natural-language math solver. |
| `POST /api/synthesize` | Bounded expression search | Toy examples only; no general program synthesis guarantee. |
| `POST /api/vision` | Vision localization | Returns 501; unsupported prototype. |
| `POST /api/blackboard` | List, inspect, or request an approximate graph summary | Graph output is structural only; no component build pipeline is wired. |
| `GET /api/blackboard/graph` | Return graph nodes, resolved links, cycles, and clusters | Approximate index; not a validated architecture model. |
| `POST /api/repos/ingest` | Shallow-clone a supplied public GitHub repo, check its root license, and cache an approximate graph | JSON body: `{"url":"https://github.com/OWNER/REPO"}`. Blocking request, 60-second clone timeout, 5,000 supported-file cap. Only accepted root licenses pass the conservative filter; no source is executed. Returns 403 under `II_PRIVATE=1`. |
| `GET /api/repos/graph?repo=OWNER/REPO[&target=SYMBOL]` | Query a graph produced by repo ingestion | Requires an existing cache; rankings and graph are approximate. |

Repository ingestion contacts GitHub and runs a local `git clone` subprocess over a
caller-supplied HTTPS GitHub URL. It parses cloned files as data, but the subprocess
is not an OS-level sandbox. Keep the unauthenticated development server local and
do not expose it to untrusted clients.

For request and response details, see the route handlers in `app.py`. This API
document intentionally describes current behavior, not planned functionality.

All network-egress endpoints (`/api/search`, `/api/fetch`, `/api/research`,
`/api/repos/ingest`, and web tool use inside `/api/chat`) are disabled when the
server is started with `II_PRIVATE=1`. See `docs/SECURITY.md`.
