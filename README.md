# II — Decentralized Internet Intelligence

> A community-hosted AI network where phones route requests and ordinary computers hold model experts—without a central GPU fleet.

[![MVP](https://img.shields.io/badge/MVP-done-c9ff52?labelColor=111315)](./network/server.js)
[![Coordinator](https://img.shields.io/badge/coordinator-Express%204.19-65e7dc?labelColor=111315)](./network)
[![Desktop](https://img.shields.io/badge/desktop-Electron%2028-9feaf9?labelColor=111315)](./client)
[![Inference](https://img.shields.io/badge/inference-v0.2%20in%20progress-ff9d5c?labelColor=111315)](#current-status)

II is being built as a network, not one centrally hosted model. Tier 1 subscriptions fund the product, while a broad host community—starting with India—supplies distributed capacity.

> **Honesty boundary:** registration, heartbeats, expert-aware routing, checkout-session creation, and the Genesis application flow are implemented. A complete 340 GB model is **not** running across the network yet. Remaining boundaries are explicitly marked `TODO-REAL` in code and below.

## Architecture

```text
┌───────────────────────────────┐
│ Phone Router                  │
│ Qwen3 4B Q4_K_M via MLC       │
│ (routing only; never a host)  │
└──────────────┬────────────────┘
               │ prompt + required expert IDs
               ▼
┌───────────────────────────────┐
│ Coordinator                   │
│ Node.js 20 + Express + SQLite │
│ registration / health / route │
└──────────────┬────────────────┘
               │ HTTPS routes (P2P begins in v0.3)
        ┌──────┴──────────────────────────────────────┐
        ▼             ▼             ▼                 ▼
   ┌────────┐    ┌────────┐    ┌────────┐       ┌────────┐
   │ PC 01  │    │ PC 02  │    │ PC 03  │  ...  │ PC 20  │
   │13 exp. │    │13 exp. │    │13 exp. │       │13 exp. │
   └────────┘    └────────┘    └────────┘       └────────┘
                  one redundant full-copy group
```

The target base is a DeepSeek-V4-style mixture-of-experts architecture: 671B total parameters, 37B active per token, 256 experts, and approximately 340 GB of quantized weights. Those are design targets, not current measured production capabilities.

## Sharding math

| Item | Calculation | Target |
|---|---:|---:|
| Total model | Provided model-weight target | ~340 GB |
| Expert size | 340 GB ÷ 256 | ~1.3 GB |
| Experts per desktop | Fixed assignment | 13 experts |
| Weight data per desktop | 13 × ~1.3 GB | ~17 GB |
| Runtime overhead | Cache, metadata, process headroom | ~3 GB |
| Desktop allocation target | ~17 GB + ~3 GB | 20 GB |
| Desktops per full copy | ceil(256 ÷ 13) | **20 devices** |
| Capacity with 400 hosts | 400 ÷ 20 | **20 full-copy groups** |

Thirteen experts across 20 devices provide 260 expert slots. The four additional slots are redundancy; a complete assignment still covers expert IDs 0–255.

## Tech stack

- **Coordinator:** Node.js 20, Express 4.19, `better-sqlite3`, plain JavaScript
- **Desktop host:** Electron 28, vanilla HTML/CSS/JS
- **Inference runtime:** `node-llama-cpp` for compatible local GGUF inference in v0.2
- **Phone router:** Qwen3 4B Q4_K_M through MLC (planned v0.4)
- **Landing:** static HTML/CSS/JS, suitable for Cloudflare Pages
- **Payments:** Dodo Payments checkout API with `pricing_mode=by_country`
- **Transport:** coordinator-provided HTTPS endpoints now; direct peer-to-peer fetch planned for v0.3
- **Storage:** SQLite coordinator state and `~/.ii/experts/` host files

## Current status

### DONE

- [x] Express coordinator starts with `node server.js`
- [x] Persistent SQLite device and Genesis application storage
- [x] Desktop registration and stable device IDs
- [x] Real `~/.ii/experts/` storage with streamed download, structural validation, and SHA-256 support
- [x] Loopback `POST /infer` service on port 7777 backed by `node-llama-cpp` when a GGUF model loads
- [x] Honest HTTP 503 `expert not loaded yet` response when no compatible model is loaded
- [x] 30-second host heartbeats with a 60-second live-routing window
- [x] Deterministic 13-expert assignments and real SQLite expert-map routing
- [x] `/register` IP throttling at 10 requests per minute
- [x] Bearer-protected Genesis CSV export at `GET /admin/genesis`
- [x] Landing-page `/stats` polling every 10 seconds with explicit offline state
- [x] Genesis form posting to the real `/genesis` persistence endpoint
- [x] Dodo checkout-session API path plus `DODO_MONTHLY_URL` / `DODO_YEARLY_URL` fallback
- [x] Idempotent Dodo `by_country` provisioning command with smallest-unit prices
- [x] Country-localized pricing displayed as $20 / $10 / ₹249, plus tax

### TODO-REAL

- [ ] Publish a production expert-0 artifact URL and checksum (tests use a local 10 MB safetensors fixture)
- [ ] Supply a compatible GGUF expert/model and prove a real inference response on target host hardware
- [ ] Build the adapter/export needed to execute raw DeepSeek expert safetensors (llama.cpp consumes GGUF, not safetensors)
- [ ] Replace coordinator-mediated HTTPS endpoints with authenticated P2P transfer
- [ ] Ship the Qwen3 4B MLC phone router and prompt-to-expert selection
- [ ] Validate full expert execution and output merging across 20 physical hosts
- [ ] Add production authentication, abuse controls, signed manifests, and encrypted peer traffic
- [ ] Publish final Genesis eligibility and profit-pool legal terms

## Roadmap

| Version | Milestone | Exit criterion |
|---|---|---|
| **v0.2** | Single-expert inference | One host stores a verified artifact and returns real local inference; unavailable models return HTTP 503 |
| **v0.3** | P2P fetch | A client fetches an expert response directly from a coordinator-selected host |
| **v0.4** | Phone router | Offline Qwen3 4B MLC router selects expert IDs and calls live routes |
| **v1.0** | 20-device full copy | All 256 experts are covered and exercised across one physical 20-host group |

## Run locally

Requirements: Node.js 20+, npm, and a desktop environment for Electron. Configuration templates live in [`.env.example`](./.env.example), [`network/.env.example`](./network/.env.example), and [`client/.env.example`](./client/.env.example). The MVP reads the process environment directly; export the values through your shell or deployment platform.

### 1. Coordinator

```bash
cd network && npm ci && npm start
```

The API and landing page are available at `http://localhost:8787`.

### 2. Desktop host

```bash
cd client && npm ci && npm start
```

Set `II_COORDINATOR_URL` first if the coordinator is not at `http://localhost:8787`.

### 3. Static web preview

```bash
cd web && python3 -m http.server 8080
```

When web and API use different origins, set the `ii-api-base` meta value in `web/index.html` to the public coordinator origin and include the page origin in `ALLOWED_ORIGINS`.

## API overview

| Method | Route | Purpose |
|---|---|---|
| `POST` | `/register` | Register a host and receive its expert range |
| `POST` | `/heartbeat` | Refresh host health and advertised expert range |
| `GET` | `/stats` | Read live network coverage and Genesis counts |
| `GET` | `/route?experts=0,1` | Find live hosts for required expert IDs |
| `POST` | `/genesis` | Submit or refresh a Genesis host application |
| `GET` | `/api/checkout/:plan` | Create or redirect to monthly/yearly Dodo checkout |

Every public route also has an `/api/...` alias for the static landing page.

## Dodo Payments setup

1. In Dodo Payments, create two recurring products:
   - **II Pro Monthly:** USD `2000` ($20.00), monthly.
   - **II Pro Yearly:** USD `19200` ($192.00), yearly.
2. Set `pricing_mode` to `by_country` on both products.
3. Add localized prices in the smallest currency unit:
   - India (`IN`): monthly INR `24900` (₹249.00), yearly INR `199900` (₹1,999.00).
   - Tier 2 countries such as `BR`, `ID`, `PH`, `TR`, and `ZA`: monthly USD `1000`, yearly USD `9600`.
   - Do not add overrides for Tier 1 fallback countries such as `US`, `GB`, `CA`, `AU`, and `DE`; they use the base USD price.
4. Copy `network/.env.example` values into your deployment environment.
5. Set `DODO_PAYMENTS_API_KEY`, `DODO_MONTHLY_PRODUCT_ID`, and `DODO_YEARLY_PRODUCT_ID`.
6. Run `cd network && npm run configure:dodo` to verify base prices and idempotently apply the India and Tier 2 rules. Use `DODO_CONFIGURE_DRY_RUN=true` to preview writes.
7. Alternatively, set pre-created payment links with `DODO_MONTHLY_URL` and `DODO_YEARLY_URL`.
8. Start in `test_mode`, complete a test checkout, then switch to `live_mode` only after webhook and entitlement handling are configured.

Taxes are calculated at checkout. Displayed prices therefore include the note **“+ tax.”**

## Genesis 400

The first 400 **verified, successfully registered hosts** are intended to receive:

- lifetime II Pro while the published host requirements continue to be met,
- a permanent Genesis host number, and
- participation in a pool equal to 5% of qualifying network profit.

The year-one target is $25 per qualifying host, not guaranteed income or an investment return. A form submission does not reserve a Genesis number; the coordinator assigns one only after the desktop host registers. Final eligibility, jurisdiction, tax, and distribution terms must be published before launch.

## Repository layout

```text
network/   Express coordinator, SQLite schema, payment handoff
client/    Electron desktop host
web/       Static Cloudflare Pages landing page
archive/   Earlier research prototypes retained for reference
```

## License

See [LICENSE](./LICENSE).
