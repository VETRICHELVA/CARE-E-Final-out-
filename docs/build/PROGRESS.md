# CARE-E build progress

Updated by `/build-section` at the end of each section. Status: `todo`, `in progress`, `done`, or `cut`.

| ID | Section | Status | Done on | Notes |
|---|---|---|---|---|
| S01 | Monorepo and infrastructure | done | 2026-10-05 | Postgres host port defaults to 5434 (Redis 6379, MQTT 1883); CI validated with actionlint (act not installed) |
| S02 | Hub foundation: auth, orgs, roles, audit | done | 2026-10-05 | 62 hub tests on a separate `care_test` DB + Redis DB 15; spec Conventions gained 429 `rate_limited` and named error codes |
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
| S14 | IoT telemetry pipeline | in progress | | Part 1 done 2026-10-05: firmware compiles (esp32dev, espressif32@7.1.3), simulator verified on the broker (excursion 9.1/9.4 °C after 60 s), CI `firmware` job. Ingest subscriber, hub Device/SensorReading, endpoints, shipment linking and delivery-web device column wait for S02/S11. Hardware test pending |
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
- [S02 → S03] `make client` is still the S03 placeholder, so no client was generated in S02. `GET /orgs/{id}` returns `OrgOut | PublicOrgView` and `GET /orgs/{id}/facilities` returns `Page[FacilityOut] | Page[PublicFacilityView]` (own org vs other org); the generated types are unions.
- [S02 → S03] Starlette's `TestClient` now warns that it wants `httpx2`; hub tests use `httpx.AsyncClient` + `ASGITransport` instead. Don't introduce `TestClient`.
- [S02 → S06/S09] `audit.record()` writes one row with one `org_id` (defaults to the actor's org; required for system actors). Cross-org actions must decide which org(s) get a row.
- [S02 → S19] `Organization.status = SUSPENDED` is stored but not enforced anywhere; it waits for a business rule. Only `User.is_active = false` blocks login.
- [S02 → S20] `JWT_SECRET` has a dev default in `app/config.py`; hardening should refuse to start without a real secret outside dev. `make seed` loads the minimal S02 seed (platform admin, Hospital A/B users, password `$SEED_PASSWORD` or `care-e-dev`); S20 replaces it.
- [S02 → S20] The `care` DB role is a superuser/table owner and can bypass the audit_log trigger; use a separate app role and REVOKE UPDATE, DELETE, TRUNCATE ON audit_log.
- [S02 → S20] Login rate limit keys on request.client.host; behind a proxy, use the forwarded client IP.
- [S02 → any] Root `ruff.toml` has no `src` hint, so ruff sorts `app` imports as third-party in `tests/` and `migrations/` (cosmetic). Adding `src = ["services/*"]` would change import order across all services.
- [S02 → S07] Consider a CI `alembic check` step (model/migration drift guard); it passes locally today.
- [S01 → S11] OSRM map data goes in `infra/osrm/` (gitignored); start it with `docker compose -f infra/docker-compose.yml --profile routing up -d osrm`.
- [S13/S17 → S04] Product codes are not in the specs (only names; "SK-A" is only a synonym in the S17 brief). Once the S04 catalog sets codes, switch `services/ai-service/evals/chat_orders.jsonl` from product names to codes.
- [S13/S17 → S04] Set `default_min_shelf_life_days` for Rapid Diagnostic Kit and IV Cannula 20G; the specs give it only for Surgical Kit A (30). The chat evals read it through `$product_default`.
- [S13/S17 → S04] Decide on sibling products (e.g. other IV cannula gauges, a second surgical kit); each adds an ambiguity case to the chat evals. Don't add a second "rapid … kit", or eval o07 ("200 rapid kits") becomes a question.
- [S04 → S17] Synonyms must resolve "SK-A", "surgical kit A", "kit A", "rapid kits" and "20G cannula" as the chat evals expect; plain "kits" must score Surgical Kit A and Rapid Diagnostic Kit within 10% of each other (evals o14–o16).
- [S13 → S20] The copilot eval runner must drive Scenario 1 to step 2, 4 or 7 (`after_step`); step 3 must decline B without a reason, because c08 expects "No reason was entered."
- [S14 → S01/S20] Mosquitto is bound to 127.0.0.1 with anonymous access, so a real ESP32 cannot reach it; the firmware README describes a temporary `socat` LAN forward. Decide whether the demo needs a LAN listener with username/password (and MQTT credentials in the firmware secrets).
- [S14 → S14] The brief's Verify line `python scripts/simulate_telemetry.py ...` fails without paho; use `uv run scripts/simulate_telemetry.py ...` (PEP 723 metadata installs paho).
- [S14 → S15] Firmware skips DS18B20 error readings (-127 not found, 85.0 power-on) instead of publishing them, so a failed probe shows in the hub as DEVICE_SILENT, not as an excursion.

## Known gaps
<!-- Things knowingly left incomplete, with the reason. -->
- S01: CI has not run on GitHub yet (no remote). `ci.yml` was validated with actionlint and its steps were run locally (`uv sync --locked`, ruff, mypy, pytest, `pnpm install --frozen-lockfile`, lint, typecheck, test).
- S02: CI postgres/redis service containers were validated with actionlint (via Docker), not on GitHub (still no remote).
- S02: The state-machine 409 is proven through a test-only route (`/api/v1/_test/transition`, mounted only in the test app); no S02 endpoint has a domain state machine.
- S01: Prettier skips the S00 kit prose (`docs/`, `.claude/`, `CLAUDE.md`) because those files aren't Prettier-formatted, and reformatting would rewrite the specs.
- S14: The manual hardware test (firmware/cold-box/README.md, "Manual hardware test") is pending until the ESP32 and DS18B20 arrive. Firmware is compile-verified only; the CI `firmware` job was validated with actionlint and run locally, not on GitHub.

## Hardening checklist (S20)
<!-- Record the outcome of each check from S20. -->
