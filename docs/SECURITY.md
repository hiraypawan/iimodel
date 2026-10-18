# II v2.0 — Security notes

II is a **local prototype**. The Flask development server has no
authentication, no TLS, and no multi-user isolation. Run it only on a trusted
machine, bound to `127.0.0.1` (the default), and never expose it to untrusted
clients or the public internet.

## Private mode: `II_PRIVATE=1`

```bash
II_PRIVATE=1 python app.py        # PowerShell: $env:II_PRIVATE="1"
```

When enabled (values `1/true/yes/on`):

- Model tools `search_web`, `read_url`, and `research` are refused, and web
  tools are no longer offered to the model.
- HTTP routes `/api/search`, `/api/fetch`, `/api/research`, and
  `/api/repos/ingest` return **403**.
- The agent's forced-search and search-synthesis fallbacks are skipped.
- Repo ingestion also refuses when called from the CLI/library.
- Local-only features keep working: GGUF/LM Studio chat, repository graph and
  topology, blackboard, static verification, Z3 explicit formulas, bounded
  synthesis, and code execution (subject to the sandbox settings below).

Consequences: agentic mode (which answers purely from web search) cannot
answer under private mode without a local model backend, and current-events
answers will be stale. `GET /api/status` reports `private_mode` and
`web_tools_enabled` so the UI/operators can verify the state. LM Studio, if
used, must itself be local for the setup to be network-free; a GGUF backend
makes no network calls in II's code.

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
or `.blackboard.json` — all are gitignored. If you find a security issue in
this prototype, please open a GitHub issue describing the affected endpoint
and configuration (do not include secrets or private code).
