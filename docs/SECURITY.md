# II v3.0 — Security notes

II is a **local research tool**. Run it on a trusted machine, bound to
`127.0.0.1` (the default), and never expose it to untrusted clients or the
public internet without the precautions below.

## API authentication: `II_API_KEY`

```bash
II_API_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(32))") \
  python app.py
```

When `II_API_KEY` is set, **every** `/api/*` route requires it. Send it as
either header:

```
X-II-API-Key: <key>
Authorization: Bearer <key>
```

A request without a valid key is answered with **HTTP 401** and a JSON body
explaining what to send. The comparison is constant-time (`hmac.compare_digest`).

When `II_API_KEY` is **not** set the API is unauthenticated. That is only
acceptable on a machine where the server is bound to localhost. `GET
/api/status` always reports which mode is active (`api_auth`), and starting
the server with a non-localhost `--host` prints a loud warning.

The chat page never embeds `II_API_KEY` in HTML. When authentication is
enabled, enter the II server key in the page's settings; it is retained only in
tab-scoped `sessionStorage` and sent as `X-II-API-Key`. This keeps merely
loading the page from disclosing the server credential.

## OpenAI-compatible BYOK

* UI-supplied provider URL, model, and key are kept in browser `sessionStorage`
  (the current tab only) and sent with chat requests. II does not write them to
  disk, logs, response bodies, memory, or tool traces.
* Server-side deployments may use `II_CHAT_API_URL`, `II_CHAT_MODEL`, and
  `II_CHAT_API_KEY`; `/api/status` reveals only whether configuration exists.
* User-supplied endpoints must resolve to a public HTTP(S) address. Redirects
  are rejected. Private/loopback endpoints require the operator-only
  `II_CHAT_ALLOW_PRIVATE_ENDPOINT=1` override.
* The provider receives conversation text and tool outputs. Choose a provider
  whose privacy and retention policy you accept.
* Hybrid chat exposes only a safe tool allowlist. It never offers arbitrary
  code execution to the hosted model.
* Protect an exposed II server with `II_API_KEY`; otherwise another user could
  submit requests using server-side BYOK credentials.

## Binding and exposure

* The server binds `127.0.0.1` by default.
* `python app.py --host 0.0.0.0` prints a large warning before binding: the
  API performs live web requests, executes code in the configured sandbox
  mode, and (unless `II_API_KEY` is set) has no authentication.
* There is no TLS. Put the server behind an authenticating reverse proxy if
  it must be reachable from another machine.

## Private mode: `II_PRIVATE=1`

```bash
II_PRIVATE=1 python app.py        # PowerShell: $env:II_PRIVATE="1"
```

When enabled (values `1/true/yes/on`):

- Internet tools `search_web`, `read_url`, and `research` are refused, hosted
  BYOK calls are blocked, and web stages are no longer offered to the router.
- HTTP routes `/api/search`, `/api/fetch`, `/api/research`, and
  `/api/repos/ingest` return **403**.
- The agent's forced-search and search-synthesis fallbacks are skipped.
- Repo ingestion also refuses when called from the CLI/library.
- Local-only features keep working: conversational replies, repository graph
  and topology, blackboard, static verification, Z3 explicit formulas, bounded
  synthesis, and code execution (subject to the sandbox settings below).

Consequences: factual questions that require external evidence fail closed
under private mode, and current-events questions cannot be answered.
`GET /api/status` reports `private_mode` and `web_tools_enabled` so the
UI/operators can verify the state.

## SSRF guard on URL fetching

`read_url` / `fetch_url_metadata` (and therefore `/api/fetch` and the research
pipeline) validate every URL with `ii_security.validate_public_url` *before*
fetching:

- Only `http://` and `https://` are allowed; embedded credentials are refused.
- `localhost`, `*.localhost`, `*.local`, `*.internal`, `*.home.arpa`, and
  cloud-metadata hostnames are refused.
- Hostnames are resolved and refused if **any** resolved address is private,
  loopback, link-local, multicast, reserved, unspecified, or a known
  cloud-metadata IP (e.g. `169.254.169.254`).

Residual risks (not fully solved):

- **Redirects:** the underlying fetcher follows redirects; redirect targets
  are not re-validated, so a public URL can redirect to a private one.
- **DNS rebinding / TOCTOU:** the address checked at validation time can
  differ from the address used at connect time.
- Fetched page content is untrusted data and is only ever parsed, never
  executed — but it is shown to the model/user, so prompt-injection via web
  content remains possible.

Treat these as reasons to keep the server local and prefer `II_PRIVATE=1`
when web access is not needed.

## Code execution

`execute_code` runs model- or user-supplied Python. Control it with:

| Setting | Effect |
|---|---|
| *(default)* | Resource-limited subprocess: temp working directory, scrubbed environment, timeout, output cap, POSIX CPU/memory/file limits where available. Runs **as your user account — this is not a sandbox.** |
| `II_CODE_SANDBOX=docker` | Fail-closed Docker run: `--network none`, `--read-only`, `--cap-drop ALL`, `no-new-privileges`, 256 MB memory, 0.5 CPU, 64 pids, non-root user, tmpfs `/tmp`. Image: `II_SANDBOX_IMAGE` (default `python:3.11-slim`). If Docker or the image is unavailable the execution fails; it never falls back silently. |
| `II_REQUIRE_SANDBOX=1` | Refuses the default subprocess mode entirely. Combine with `II_CODE_SANDBOX=docker`. |

Docker mode still executes arbitrary code inside the container; keep the
Docker daemon itself trusted and the image pinned/updated. No mode makes it
safe to expose II to untrusted users.

### Generated and fetched code is Docker-only

`tools/self_tool_builder.py` and `core/critic_loop.py` handle code that came
from the internet or from the model. That code is **never** executed in the
default subprocess mode:

* The self-tool builder refuses to run anything unless
  `II_CODE_SANDBOX=docker` is set. There is no silent fallback — the run
  fails and the code is not trusted.
* Generated code must match a strict template: allowlisted imports only, a
  mandatory `try`/`except` block, an entry point, and network access only
  through the SSRF-guarded fetcher in `ingestion/ii_web.py`.
* The critic loop then runs the code in the sandbox, scans it with Bandit
  (a *skipped* scan is reported as skipped, never as passed) and checks it
  against the Interviewer's constraint tree.

## Memory and privacy

The Exocortex (`memory/exocortex.py`) stores facts, episodes and tool-run
bookkeeping on disk. It is **opt-in**:

* Nothing is written before the user grants consent.
* Proposed profile items are stored only after the user approves each one.
  Silent background profiling is forbidden by design and is enforced in code
  (`Exocortex.remember` refuses to write without consent).
* `GET /api/memory` shows exactly what is stored; `POST /api/memory/forget`
  and `python main.py memory --action forget --key NAME` delete it.
* Revoking consent deletes the stored facts.
* The storage location defaults to `~/.ii_exocortex` and can be moved with
  `II_DATA_DIR`.

The memory directory is local to the machine. It is not encrypted; do not
store secrets in it.

## Untrusted content

Fetched web pages, search snippets and repository source are **untrusted
data**. They are parsed, never executed, but they are shown to the model and
to the user, so prompt injection through web content remains possible. The
caged renderer limits the blast radius: the model may only rephrase the JSON
it is given, and any entity it invents is detected and the rendering is
rejected.

## Repository ingestion

`/api/repos/ingest` and `python main.py ingest` accept only public
`https://github.com/OWNER/REPO` URLs (no credentials, ports, queries, or
fragments), shallow-clone with a timeout and file-count cap, check the root
license conservatively, parse source as data (never import/execute it), and
cache the graph as JSON. Remaining risk: `git clone` itself is a local
subprocess processing remote-controlled repository metadata; ingestion is
blocked under `II_PRIVATE=1`.

## Reporting state

Do not commit virtual environments, model weights, `.cache/`, `.ii_state/`,
`.blackboard.json`, `.ii_server.pid` or `self_tools/` — all are gitignored.
The Exocortex database lives outside the repository by default
(`~/.ii_exocortex`); if you point `II_DATA_DIR` into the repository, keep it
out of version control. If you find a security issue in
this prototype, please open a GitHub issue describing the affected endpoint
and configuration (do not include secrets or private code).
