# CARE-E

A healthcare supply shortage-resolution network. A hospital reports a shortage; the hub looks for
stock that is **actually transferable** at other hospitals or at suppliers, asks the source to
confirm and hold it, and recommends TRANSFER, TRANSFER_SPLIT or BUY. A person approves, and the
system tracks the delivery (with IoT cold-chain monitoring) through receipt and reconciliation.
An AI copilot explains what happened and drafts shortages from chat, but never decides.

## Non-negotiable rules

1. **The hub decides; apps display.** Business rules, permissions and state transitions live only
   in `services/hub-api`.
2. **AI assists, never decides.** Read-only tools; it drafts but never commits, and states no
   figure a tool didn't return.
3. **Humans approve** transfers, purchases, holds and receipts.
4. **Recorded ≠ transferable.** Only computed transferable quantity is offered to the network.
5. **Honest audit.** Every state change writes an append-only audit row; no invented reasons or
   physical events.
6. **Org isolation.** Every query is scoped to the caller's org; other orgs see only transferable
   quantity, expiry band and location.

## Quickstart (about 10 minutes)

### Prerequisites

- Docker with Compose v2 (Postgres 16, Redis 7 and Mosquitto run in containers)
- [uv](https://docs.astral.sh/uv/) and Python 3.12
- Node.js 24 and pnpm (`corepack enable` picks up the version pinned in `package.json`)
- `make` and `git`

### Install, start and seed

```sh
make install    # uv sync for each service, pnpm install, Playwright Chromium, pre-commit hooks
make up         # Postgres (host port 5434), Redis (6379), Mosquitto (1883)
make migrate    # Alembic migrations
make seed       # demo orgs, users, catalog, fleet and cold box (docs/specs/demo-scenarios.md)
```

To start again from a clean, freshly seeded database, use `make demo-reset` (wipes the database
and seeds again). Every service has working local defaults, so no `.env` file is needed for a
local demo; the `.env.example` files list what can be overridden.

### Run

Each of these runs in its own terminal:

```sh
make hub        # hub API on http://localhost:8000 (OpenAPI docs at /api/v1/docs)
make worker     # timers, live updates (SSE) and webhooks; the apps are not live without it
pnpm --filter hospital-web dev   # http://localhost:5173
pnpm --filter supplier-web dev   # http://localhost:5174
pnpm --filter delivery-web dev   # http://localhost:5175
```

Optional:

```sh
make ingest     # MQTT cold-box telemetry -> hub, for the cold-chain scenario
uv run scripts/simulate_telemetry.py --device cb-01 --profile excursion --interval 5
AI_API_KEY=sk-... make ai   # copilot and chat ordering on :8100 (ANTHROPIC_API_KEY also works)
```

Without a key the AI service still starts and the copilot panel says "AI is not configured"; the
rest of the system works the same. For road routes instead of straight-line estimates, see
`infra/osrm/README.md` and set `OSRM_URL` for the hub.

### Demo logins

Every user's password is `demo1234` (development only; override with `SEED_PASSWORD` before
seeding). Emails follow `<role>@<org>.demo`:

| App          | Who                      | Email                                                                 |
| ------------ | ------------------------ | --------------------------------------------------------------------- |
| hospital-web | Hospital A store manager | `store.manager@hospital-a.demo` (reports shortages, answers requests) |
| hospital-web | Hospital A approver      | `approver@hospital-a.demo` (approves, reads the audit trail)          |
| hospital-web | Hospital A receiver      | `receiver@hospital-a.demo` (records receipts)                         |
| hospital-web | Hospital B to F          | same roles at `hospital-b.demo` … `hospital-f.demo`                   |
| hospital-web | Platform admin           | `admin@care-e.demo`                                                   |
| supplier-web | Supplier X, Y, Z desk    | `supplier.desk@supplier-x.demo` (and `supplier-y`, `supplier-z`)      |
| delivery-web | SwiftMed dispatcher      | `dispatcher@swiftmed.demo`                                            |
| delivery-web | SwiftMed drivers         | `driver@swiftmed.demo` (Ravi), `driver2@swiftmed.demo` (Priya)        |

Hospitals C to F and Suppliers Y and Z come from the full demo seed. Each org also has an
`admin@<org>.demo` user with every capability of that org. The hub allows 5
sign-ins per minute per IP address, so sign in the people you need a minute apart if you hit the
limit. Each browser tab keeps its own session, so one tab per user works.

The click-by-click demo script for all three scenarios is in
[`docs/demo-runbook.md`](docs/demo-runbook.md).

## Architecture

```text
 hospital-web :5173     supplier-web :5174     delivery-web :5175
 (React 19, Vite; each proxies /api to the hub; hospital-web also /ai to the AI service)
        |  REST + SSE              |                     |
        v                          v                     v
 +-----------------------------------------------------------------+
 | hub-api :8000  (FastAPI, SQLAlchemy async, Pydantic v2)          |
 |   rules in app/domain/ (pure functions) . org-scoped queries     |
 |   append-only audit log . event outbox -> SSE stream + webhooks  |
 +-----------------------------------------------------------------+
     |            |                 ^                 ^          |
     v            v                 |                 |          v
 PostgreSQL 16  Redis 7 <-- arq worker            ai-service   OSRM (optional)
                (timers, outbox, webhooks,        :8100        road distances
                 nightly forecasts)               GET /ai/read/* only, as the user
                                                  ^
                                    iot-ingest ---+ (POST /internal/telemetry every 2 s)
                                         ^
                                         | MQTT careE/devices/+/telemetry (Mosquitto :1883)
                                         |
                          cold box (ESP32 + DS18B20) or scripts/simulate_telemetry.py
```

- **Hub (`services/hub-api`).** The only place business rules, permissions and state machines
  live. Domain rules are pure functions in `app/domain/`; each module has `models`, `schemas`,
  `service`, `router` and `tests`. Every state change writes an audit row and an outbox event in
  the same transaction.
- **Worker (`make worker`, arq on Redis).** Response, hold and recommendation deadlines every
  30 s; publishes outbox events every second to the apps' SSE stream; webhook deliveries with
  retries; nightly forecasts and surplus expiry.
- **Apps (`apps/*-web`).** React 19 + TypeScript + Vite, Tailwind, shadcn/ui, TanStack Query.
  They reach the hub only through the generated `packages/api-client` and hide the actions a
  user can't take; the hub still checks every call.
- **AI service (`services/ai-service`).** Copilot answers and chat ordering drafts. It holds a
  read-only service token, calls only `GET /ai/read/*` as the signed-in user, and checks that
  every number it states came from a tool result.
- **IoT (`services/iot-ingest`, `firmware/cold-box`).** The cold box publishes a reading every
  10 s over MQTT; the ingest de-duplicates and posts batches to the hub, which links readings to
  the shipment and raises cold-chain excursions.
- **Routing.** OSRM for road distances and routes when `OSRM_URL` is set; otherwise, or if OSRM
  fails or is slow, straight-line distance × 1.3.

## Common commands

| Command                      | What it does                                                    |
| ---------------------------- | --------------------------------------------------------------- |
| `make test`                  | All tests (`make test-hub`, `make test-web` for one side)       |
| `make lint`                  | ruff, mypy, eslint, prettier, tsc                               |
| `make e2e`                   | Playwright tests in `e2e/` (needs `make up migrate seed`)       |
| `make client`                | Regenerate `packages/api-client` from the hub's OpenAPI         |
| `make migration m="message"` | New Alembic migration                                           |
| `make eval-ai`               | AI evals against the real model (needs a key and a running hub) |
| `make down`                  | Stop the infrastructure containers                              |

The full, current list is under "Commands" in [`CLAUDE.md`](CLAUDE.md).

## Documentation

- Specs, in [`docs/specs/`](docs/specs/):
  - [`domain-model.md`](docs/specs/domain-model.md): entities and fields
  - [`business-rules.md`](docs/specs/business-rules.md): transferable formula, gates, ranking,
    time limits, state machines
  - [`api-and-events.md`](docs/specs/api-and-events.md): REST conventions, endpoints, events,
    errors
  - [`apps-ai-iot.md`](docs/specs/apps-ai-iot.md): screens per app, AI components, IoT pipeline
  - [`demo-scenarios.md`](docs/specs/demo-scenarios.md): seed numbers and scenario steps
- Demo script: [`docs/demo-runbook.md`](docs/demo-runbook.md)
- Build plan: [`docs/build/README.md`](docs/build/README.md); section briefs in
  [`docs/build/sections/`](docs/build/sections/)
- Status, follow-ups and known gaps: [`docs/build/PROGRESS.md`](docs/build/PROGRESS.md)
- Per-service notes: `services/*/README.md`, `apps/*/README.md`, `firmware/cold-box/README.md`,
  `infra/osrm/README.md`
