# CARE-E

Healthcare supply shortage-resolution network. Hospitals report shortages; the hub finds stock that is **actually transferable** in other hospitals or suppliers, gets the source to confirm and hold it, recommends TRANSFER / TRANSFER_SPLIT / BUY, a human approves, and the system tracks delivery (with IoT cold-chain monitoring) through reconciliation.

## Non-negotiable rules
1. **The hub decides; apps display.** Business rules, permissions and state transitions live only in `services/hub-api`. Frontends just hide actions the user can't take.
2. **AI assists, never decides.** AI gets read-only tools, drafts but never commits, and states no figure a tool didn't return.
3. **Humans approve** transfers, purchases, holds and receipts.
4. **Recorded ≠ transferable.** Only computed transferable quantity is ever offered to the network.
5. **Honest audit.** Every state change writes an append-only AuditLog row. If no reason was given: `reason_source=SYSTEM`, "No reason was entered." Never invent physical events.
6. **Org isolation.** Every query is scoped to the caller's org; cross-org reads see only transferable qty, expiry band and location.

Do not change a business rule without updating `docs/specs/` in the same change, and ask first.

## Where things are
- Specs (read only the one you need): `docs/specs/`
  - `domain-model.md` — entities and fields
  - `business-rules.md` — transferable formula, gates, ranking, time limits, state machines
  - `api-and-events.md` — REST conventions, endpoints, webhook events, errors
  - `apps-ai-iot.md` — screens per app, AI components, IoT pipeline
  - `demo-scenarios.md` — exact seed numbers and scenario steps
- Build plan: `docs/build/README.md`; current status: `docs/build/PROGRESS.md`; section briefs: `docs/build/sections/`

## Stack
- Apps: React 19 + TS + Vite, Tailwind, shadcn/ui, TanStack Query, Zustand, React Router — `apps/{hospital,supplier,delivery}-web`
- Shared: `packages/ui`, `packages/api-client` (GENERATED from hub OpenAPI — never hand-edit)
- Hub: Python 3.12, FastAPI, SQLAlchemy 2 async, Alembic, Pydantic v2, arq jobs, statsmodels (forecasting) — `services/hub-api`
- AI: `services/ai-service` (FastAPI, LLM tool calling; read-only access to the hub)
- IoT: `services/iot-ingest` (paho-mqtt), `firmware/cold-box` (ESP32 + DS18B20)
- Infra: PostgreSQL 16, Redis 7, Mosquitto, OSRM via `infra/docker-compose.yml`

## Commands
(Kept current by S01 — update this list when commands change.)
- `make install` — install Python (uv) and JS (pnpm) dependencies and pre-commit hooks
- `make up` / `make down` — start/stop infra
- `make hub` — run hub API with reload
- `make worker` — run the hub's arq worker (deadline timers every 30 s; tunables in `app/domain/config.py` are overridable by env vars, e.g. `SLA_CRITICAL_RESPONSE_MINUTES=1`)
- `make test` — all tests; `make test-hub`, `make test-web`
- `make lint` — ruff, mypy, eslint, prettier, tsc
- `make migrate` / `make migration m="msg"` — Alembic
- `make client` — regenerate `packages/api-client` from the hub's OpenAPI (imports the app; no server needed)
- `make seed` — load demo data
- `make e2e` — Playwright tests in `e2e/` (needs `make up migrate seed`; starts the hub and apps unless running)
- `pnpm --filter <app> dev` — run one app (hospital-web :5173, supplier-web :5174, delivery-web :5175; proxies `/api` to the hub)

## Conventions
- Domain rules are pure functions in `services/hub-api/app/domain/`, fully unit-tested.
- Layout per hub module: `models.py`, `schemas.py`, `service.py`, `router.py`, `tests/`.
- Invalid state transition → HTTP 409; cross-org access → 403. Errors are `{code, message, details}`.
- Money as integer paise. Timestamps in UTC. UUID primary keys.
- Every endpoint test covers success, 403 from another org, and 409 where a state machine applies.
- Frontend data access only through `packages/api-client` + TanStack Query hooks.
- Conventional commits prefixed with the section ID, e.g. `S05: add shelf-life gate`.

## How we work
Development is split into 20 sections (S01–S20). Run one section per session:
- `/build-section S05` — plans, waits for your approval, builds, verifies, updates PROGRESS.md.
- `/check-section S05` — an independent read-only review against the acceptance criteria.
- The `spec-guardian` subagent reviews diffs that touch matching, holds, state machines, reconciliation, audit, auth or AI tools.

Stay inside the section's scope; note anything else in PROGRESS.md under "Follow-ups" instead of doing it. Start a fresh session (`/clear`) between sections.
