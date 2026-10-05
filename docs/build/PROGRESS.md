# CARE-E build progress

Updated by `/build-section` at the end of each section. Status: `todo`, `in progress`, `done`, or `cut`.

| ID | Section | Status | Done on | Notes |
|---|---|---|---|---|
| S01 | Monorepo and infrastructure | done | 2026-10-05 | Postgres host port defaults to 5434 (Redis 6379, MQTT 1883); CI validated with actionlint (act not installed) |
| S02 | Hub foundation: auth, orgs, roles, audit | todo | | |
| S03 | Frontend foundation and API client | todo | | |
| S04 | Catalog and inventory | todo | | |
| S05 | Shortages and the matching engine | todo | | |
| S06 | Source requests, holds and timers | todo | | |
| S07 | Events, webhooks and live updates | todo | | |
| S08 | Hospital app: inventory, shortages, requests | todo | | |
| S09 | Recommendations, approvals, purchase orders | todo | | |
| S10 | Supplier app | todo | | |
| S11 | Shipments, routing and the delivery app | todo | | |
| S12 | Receiving, reconciliation, decision screens | todo | | |
| S13 | AI service and copilot | todo | | |
| S14 | IoT telemetry pipeline | todo | | |
| S15 | Cold-chain rules and alerts | todo | | |
| S16 | Route optimization | todo | | |
| S17 | Chat ordering | todo | | |
| S18 | Forecasting and expiry surplus | todo | | |
| S19 | Critical mode, reliability and credits | todo | | |
| S20 | Demo scenarios, E2E tests and hardening | todo | | |

## Milestone gates
- **M0** (S01–S03): every app logs in against the hub; CI green.
- **M1** (S04–S08): a shortage finds eligible sources, and one accepts with a hold, in the hospital app.
- **M2** (S09–S13): the full loop from shortage to reconciliation across all three apps, with audit and copilot.
- **M3** (S14–S19): chat, forecasts, surplus, optimization, cold chain, Critical mode.
- **M4** (S20): three demo scenarios pass three times in a row.

## Follow-ups
<!-- Things noticed during a section that belong to another section or to later. Format: - [Sxx → Syy] description -->
- [S01 → S02] Postgres is published on host port 5434 (not 5432) so it never clashes with another local Postgres; hub settings should default to `services/hub-api/.env.example` (`DATABASE_URL=...@127.0.0.1:5434/care`).
- [S01 → S03] TypeScript is pinned to `~6.0.3`: typescript-eslint 8.71 only supports TS `<6.1.0`, and TS 7 is `latest` on npm. Keep apps on 6.0.x until typescript-eslint supports TS 7.
- [S01 → S03] Root `pnpm typecheck` / `pnpm test` run `pnpm -r --if-present`, so they are no-ops until apps/packages add `typecheck` and `test` scripts. Replace the `client-drift` CI placeholder with the real regenerate-and-diff check.
- [S01 → S11] OSRM map data goes in `infra/osrm/` (gitignored); start it with `docker compose -f infra/docker-compose.yml --profile routing up -d osrm`.

## Known gaps
<!-- Things knowingly left incomplete, with the reason. -->
- S01: CI has not run on GitHub yet (no remote). `ci.yml` was validated with actionlint and its steps were run locally (`uv sync --locked`, ruff, mypy, pytest, `pnpm install --frozen-lockfile`, lint, typecheck, test).
- S01: Prettier skips the S00 kit prose (`docs/`, `.claude/`, `CLAUDE.md`) because those files aren't Prettier-formatted, and reformatting would rewrite the specs.

## Hardening checklist (S20)
<!-- Record the outcome of each check from S20. -->
