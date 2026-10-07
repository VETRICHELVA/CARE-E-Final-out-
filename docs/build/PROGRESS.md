# CARE-E build progress

Updated by `/build-section` at the end of each section. Status: `todo`, `in progress`, `done`, or `cut`.

| ID | Section | Status | Done on | Notes |
|---|---|---|---|---|
| S01 | Monorepo and infrastructure | done | 2026-10-05 | Postgres host port defaults to 5434 (Redis 6379, MQTT 1883); CI validated with actionlint (act not installed) |
| S02 | Hub foundation: auth, orgs, roles, audit | done | 2026-10-05 | 62 hub tests on a separate `care_test` DB + Redis DB 15; spec Conventions gained 429 `rate_limited` and named error codes |
| S03 | Frontend foundation and API client | done | 2026-10-05 | React 19 + react-router + Tailwind v4 + shadcn; apps on :5173/:5174/:5175 call the hub through a Vite `/api` proxy (no hub CORS yet); 32 Vitest tests + 3-test Playwright login smoke (`make e2e`); client regenerated after S04 merged |
| S04 | Catalog and inventory | done | 2026-10-05 | 40-product catalog (`SURG-KIT-A`, `DIAG-RDK`, `IV-CAN-20G`); 134 hub tests; batch writes HOSPITAL-only, offer writes SUPPLIER-only, a verify count replaces on_hand (business-rules §2 updated); dev seed adds Supplier X and SwiftMed Logistics |
| S05 | Shortages and the matching engine | done | 2026-10-07 | 235 hub tests (Scenario 1 fixture, split, every gate pass/fail, every ranking tiebreak); spec-guardian fixes: shortfall 0 → 400, ranking on per-unit landed cost, hospital costs hidden from the requester (business-rules §1/§5 updated); `planned_resolution` stored on MatchRun, excluded orgs carry into later runs; cold_chain gate fails every cold-chain product until S11 adds vehicles |
| S06 | Source requests, holds and timers | done | 2026-10-07 | 286 hub tests (51 new): TRANSFER/SPLIT runs send requests, accept row-locks batches (race test on a committed DB: one 200, one 409), decline/expiry/stock-change go through `release_and_rematch`, arq worker (`make worker`) every 30 s and at startup; tunables in `app/domain/config.py` overridable by env; migration 0004 |
| S07 | Events, webhooks and live updates | todo | | |
| S08 | Hospital app: inventory, shortages, requests | in progress | | Part 1 done 2026-10-07 (against the S05 contract): inventory table (transferable emphasized, read-only), edit/verify dialogs, CSV import with per-row report; shortages list, zod "New shortage" form showing the hub's shortfall; shortage detail with timeline, ranked eligible and grouped rejected candidates (hub reason text verbatim), Re-run match and Cancel; dashboard open-shortage counts; nav hidden by capability. 48 hospital-web + 3 new ui Vitest tests. Part 2 waits for S06/S07: incoming requests, source-requests panel, dashboard countdowns, `useEventStream()`, Playwright Scenario 1 |
| S09 | Recommendations, approvals, purchase orders | todo | | |
| S10 | Supplier app | todo | | |
| S11 | Shipments, routing and the delivery app | todo | | |
| S12 | Receiving, reconciliation, decision screens | todo | | |
| S13 | AI service and copilot | todo | | |
| S14 | IoT telemetry pipeline | in progress | | Part 1 done 2026-10-05: firmware compiles (esp32dev, espressif32@7.1.3), simulator verified on the broker (excursion 9.1/9.4 °C after 60 s), CI `firmware` job. Part 2 done 2026-10-07: `services/iot-ingest` (44 tests, no broker), hub Device/SensorReading (migration 0005), `POST /internal/telemetry` (ingest token, idempotent), `GET /devices`, dev seed registers `cb-01`; 259 hub tests. Live smoke on a temporary amqtt broker (Mosquitto not installed): 12 simulator readings stored with max 2.2 s lag; replay through the ingest posted nothing, replay after an ingest restart returned 12 duplicates. Shipment linking, `POST /devices/{id}/assign`, `coldchain.reading` and the delivery-web device column wait for S11/S07. Hardware test pending |
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
- [S02 → S20] `JWT_SECRET` has a dev default in `app/config.py`; hardening should refuse to start without a real secret outside dev. `make seed` loads the minimal dev seed (platform admin, Hospital A/B, Supplier X and SwiftMed Logistics users, emails `<role>@<org>.demo` and password `$SEED_PASSWORD` or `demo1234` as in demo-scenarios.md, plus the S04 catalog); it is idempotent per org name. S20 replaces it.
- [S02 → S20] The `care` DB role is a superuser/table owner and can bypass the audit_log trigger; use a separate app role and REVOKE UPDATE, DELETE, TRUNCATE ON audit_log.
- [S02 → S20] Login rate limit keys on request.client.host; behind a proxy, use the forwarded client IP.
- [S02 → any] Root `ruff.toml` has no `src` hint, so ruff sorts `app` imports as third-party in `tests/` and `migrations/` (cosmetic). Adding `src = ["services/*"]` would change import order across all services.
- [S02 → S07] Consider a CI `alembic check` step (model/migration drift guard); it passes locally today.
- [S01 → S11] OSRM map data goes in `infra/osrm/` (gitignored); start it with `docker compose -f infra/docker-compose.yml --profile routing up -d osrm`.
- [S13/S17 → S04] Resolved in S04 (recorded in demo-scenarios.md): codes `SURG-KIT-A`, `DIAG-RDK`, `IV-CAN-20G`; `default_min_shelf_life_days` 30 / 60 / 30, and 30 for every other product (IV Cannula 20G must stay below 55 so Scenario 3's +55-day batch passes); no sibling products, and only those two names contain "Kit". A catalog test guards the names.
- [S04 → S13/S17] Switch `services/ai-service/evals/chat_orders.jsonl` and its README from product names to the codes above (ai-lead's files).
- [S04 → S17] Synonyms must resolve "SK-A", "surgical kit A", "kit A", "rapid kits" and "20G cannula" as the chat evals expect; plain "kits" must score Surgical Kit A and Rapid Diagnostic Kit within 10% of each other (evals o14–o16).
- [S13 → S20] The copilot eval runner must drive Scenario 1 to step 2, 4 or 7 (`after_step`); step 3 must decline B without a reason, because c08 expects "No reason was entered."
- [S14 → S01/S20] Mosquitto is bound to 127.0.0.1 with anonymous access, so a real ESP32 cannot reach it; the firmware README describes a temporary `socat` LAN forward. Decide whether the demo needs a LAN listener with username/password (and MQTT credentials in the firmware secrets).
- [S14 → S14] The brief's Verify line `python scripts/simulate_telemetry.py ...` fails without paho; use `uv run scripts/simulate_telemetry.py ...` (PEP 723 metadata installs paho).
- [S14 → S15] Firmware skips DS18B20 error readings (-127 not found, 85.0 power-on) instead of publishing them, so a failed probe shows in the hub as DEVICE_SILENT, not as an excursion.
- [S14 → S14 part 3, after S11] Add `Device.assigned_shipment_id` (FK shipment) and `SensorReading.shipment_id`; link new readings in `app/iot/service.py:after_store` only while the shipment is ASSIGNED, PICKED_UP or IN_TRANSIT; build `POST /devices/{id}/assign` (`shipment.assign`, own org's device, audit row, 403 cross-org) and the delivery-web device column and assign action (Fleet and shipment detail).
- [S14 → S07] Emit `coldchain.reading` {shipment_id, temp_c, ts} from `app/iot/service.py:after_store` for each linked reading. Readings for unlinked devices emit nothing.
- [S14 → S15] Cold-chain rules plug into the same `after_store` hook. `Device.last_seen` is the newest reading's device ts, capped at the hub's now and never moved backwards; S15's silent check can use it.
- [S14 → S20] `INGEST_TOKEN` has a dev default in both `services/hub-api/app/config.py` and `services/iot-ingest/app/config.py`, and the hub accepts it. Hardening must refuse to start without a real one outside dev, as with `JWT_SECRET`: a leaked default lets anyone post fake readings, which S15 would turn into fake excursions. The full seed must register every demo device under its logistics org; readings from an unregistered device_id are reported as `unknown_devices` and not stored.
- [S14 → S14] The brief's Verify line `(cd services/iot-ingest && uv run python -m iot_ingest)` does not match the service package (`app`, like the other services). Use `make ingest`, or `uv run python -m app` in `services/iot-ingest`.
- [S14 → S03/client-drift] `make client` in S14 also added S05's shortage endpoints to `packages/api-client`, which the S05 commit had not regenerated.
- [S04 → S06] Batch responses use `batch_transferable(..., held_qty=0)` (`BatchOut.of`); once holds exist, pass the batch's active holds so `transferable` in `GET /inventory/batches` matches what matching offers.
- [S04 → S07] Batch and offer changes emit no events yet; business-rules §5 re-runs MATCHING shortages "when inventory or offers change", so retrofit `emit` into `app/inventory/service.py` and `app/catalog/service.py:put_offer`.
- [S04 → S20] The dev seed has no inventory batches, supplier offers or ProductAuthorizations, so until S20 the S05 authorization gate needs test-made rows (demo: everyone authorized except Hospital E for Surgical Kit A).
- [S04 → S08] CSV import takes the file as a raw `text/csv` body (`?facility_id=`); with openapi-fetch, send the `File` with a passthrough `bodySerializer`. `GET /inventory/batches` has no product or facility filter, and batches carry only `product_id` (join with `GET /products?limit=200`).
- [S04 → later] Expiry uses the UTC date, so between 00:00 and 05:30 IST a batch that expires that IST day still counts as transferable. Switch to Asia/Kolkata if that matters for the demo.
- [S04 → later] CSV import uses one savepoint and one audit row per line (1 MB cap); fine for store-sized files, but batch it if imports grow.
- [S04 → S05+] Inputs that reach Postgres use the shared types in `app/db.py`: `NonNegInt4` for every qty, paise and hours field (int4 columns), and `NulFreeStr` (or the `NulFree` validator) for free text, since Postgres text cannot hold NUL. As a safety net, a SQLSTATE class 22 error maps to 400 `validation` (`app/errors.py`, `is_data_error`), and only a unique violation (23505) becomes 409 in `flush_or_conflict`. Verify now requires `method` (the generated client type changes when it is regenerated).
- [S03 → hub] `OrgOut.type`/`PublicOrgView.type` are `str` and `MeOut.roles`/`capabilities` are `list[str]`, so the generated client types are plain `string`. Declaring them as enums would type-check the apps' org-type gate and `can()`.
- [S03 → S07] Query keys are the API path (e.g. `["/api/v1/auth/me"]`), so the `/events/stream` handler can invalidate by path.
- [S03 → S08] Decide on typed query-hook helpers (openapi-react-query is not approved yet). S03 provides `unwrap()`, `ApiError`, `createQueryClient()` and `useMe()`.
- [S03 → S08/S10/S11] The nav shows every route to every role (e.g. delivery-web "Driver jobs" is for DRIVER); hide entries by capability when the screens land.
- [S03 → S20] The login rate limit (5/min/IP, every attempt counts) applies to local e2e runs; the smoke test uses 3 logins, so a second `make e2e` within ~60 s gets 429. "make e2e ×3" needs a test-configurable limit or a pause.
- [S03 → S20] e2e is not in CI (it needs Postgres, Redis and a seeded hub); add a CI e2e job.
- [S05 → S06] Matching calls `batch_transferable(r, today)` with no held qty; count active holds as `reserved` (§2) once holds exist. `cancel_shortage` must release holds and supersede requests then. `run_match(..., exclude=[org])` is the hook for a decline/expiry re-run.
- [S05 → S07] Shortage transitions and match runs emit no `shortage.status_changed` events yet; a "No eligible source" shortage stays MATCHING and needs the S07 re-run on inventory/offer change.
- [S05 → S11] `COLD_CHAIN_VEHICLE_ON_RECORD = False` in `app/shortages/service.py`: with no Vehicle table every cold-chain product (e.g. Rapid Diagnostic Kit, Scenario 2) fails the cold_chain gate. Replace with a cold-chain Vehicle query, and swap `ROUTING` to OSRM with haversine fallback.
- [S05 → S19] Candidate reliability is always `DEFAULT_RELIABILITY` (70); read ReliabilityScore once it exists.
- [S05 → S09] Match runs hide `landed_cost_paise` for hospital sources (candidates and plan lines; stored values keep it). Recommendation `lines` must decide the same way before showing per-line cost to the requester, e.g. show only the total, or show transport and fees without the item value.
- [S05 → S20] Shelf life at delivery uses `(now + eta).date()`, so a run whose delivery lands after 00:00 UTC reports Hospital D as "Expires in 11 days" instead of Scenario 1's "12 days". The S05 test pins `now` at 06:00 UTC; the S20 seed or demo must pin the time of day too, or relax that wording check.
- [S05 → S08] `GET /shortages/{id}/match-runs/latest` returns eligible candidates by rank, then rejected ones with every gate's result; `planned_resolution` is null with `reason: "No eligible source"` when nothing is eligible.
- [S03 → S20] Apps reach the hub through a Vite dev proxy, so the hub has no CORS yet; restrict CORS to the three app origins for any non-proxied deployment.
- [S08 → S08 part 2] After S06: `/requests` screen (product, qty, requesting hospital, deadline countdown, Accept, Decline with `ConfirmDialog`'s optional reason; nav entry already gated by `source_request.respond`), the source-requests panel on shortage detail, dashboard "incoming requests awaiting response" with countdowns. After S07: replace `POLL_MS` polling (`apps/hospital-web/src/api.ts`, 10 s `refetchInterval`) with `useEventStream()`. Then the Playwright Scenario 1 test (two contexts: A creates, B declines, A's detail re-runs); `make e2e` needs Chromium: in the cloud container it is preinstalled (`PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers`, executable `/opt/pw-browsers/chromium`), so don't run `playwright install` there.
- [S08 → S03/S10/S11] Query hooks live in `apps/hospital-web/src/api.ts` (no openapi-react-query). Keys are `[path template, params]`, e.g. `["/api/v1/shortages/{shortage_id}", {shortage_id}]`, so S07 can invalidate by path prefix. Move the helpers to `packages/ui` or `packages/api-client` once a second app needs them.
- [S08 → S09/S12] Resolved 2026-10-07: `GET /shortages` and `GET /shortages/{id}` accept `shortage.create` or `recommendation.approve` (`require_any` in `app/auth/deps.py`), so approvers read their org's shortages and see the Shortages nav and dashboard card (`SHORTAGE_READERS` in hospital-web). Writes stay `shortage.create`. Receivers still cannot read shortages; decide in S12 whether the receiving screen needs it.
- [S08 → hub] The dashboard counts open shortages from the first 200 of `GET /shortages` (no status filter or counts endpoint). Add `?status=` or a summary endpoint if an org can have more than 200 shortages.
- [S08 → CLAUDE.md] hospital-web now depends on zod 4 (the S08 brief asks for zod form validation; it was already in the lockfile via eslint-plugin-react-hooks). The CLAUDE.md stack list doesn't name zod; add it there if that list is meant to be complete.
- [S08 → S20] A user without `audit.read` (store manager, requester) sees a timeline built only from the shortage's own `created_at`/`updated_at` and the latest match run; the full status history needs `audit.read`. Decide whether requesters should see their shortage's status history.
- [S06 → S07] Hook points marked `# S07: emit(...)`: `source_request.created` in `app/source_requests/holds.py:create_requests`, `source_request.status_changed` in `holds.move_request` (every request transition passes through it). Shortage transitions still go through `shortages.service.move_shortage`.
- [S06 → S09] `on_sources_ready(session, shortage, run)` in `app/source_requests/hooks.py` is a no-op TODO; it runs once every request of a run's plan is TENTATIVE_HOLD, and right after a BUY run (§7 step 4 "immediately for BUY"). Approval should use `holds.move_hold` (TENTATIVE → FIRM) and `holds.move_request` (→ CONFIRMED); rejection or recommendation expiry should call `service.release_and_rematch(..., trigger=Trigger.RECOMMENDATION_EXPIRED)`, which already moves AWAITING_DECISION or IN_FULFILLMENT back to MATCHING. Hold rows already carry `expires_at`; the timer expires TENTATIVE_HOLD requests when a tentative hold lapses.
- [S06 → S08] `GET /source-requests?direction=incoming|outgoing` (required) with optional `status`, `shortage_id`. The requester's view has `holds: null` (the source's batch ids stay private) but `held_qty` and `hold_expires_at`. Batches gained read-only `held_qty`; `transferable` is net of active holds. Accept's 409 `conflict` has `details: {requested_qty, transferable_qty}`; a late answer is 409 `invalid_transition` and the request comes back EXPIRED. A manual re-run while requests are open is 409 `conflict` with `details.open_source_request_ids`.
- [S06 → S09/S20] Audit org per row (`audit.record` writes one row): request transitions go to the requester's org (its shortage's trail shows B's decline, Scenario 1 step 8); hold rows go to the source org. So the source's `GET /audit` shows its holds but not its own accept/decline rows. Decide whether cross-org transitions should write one row per involved org.
- [S06 → S19] CRITICAL parallel requests: the partial unique index `uq_source_request_open` allows one open request per (shortage, source org). `holds.release` supersedes every open request of the shortage; S19's "first to accept wins" must supersede only the siblings and keep the winner's holds.
- [S06 → S11] PATCH or verify can drop a batch's `on_hand` below its active holds (transferable then shows 0, but the hold stays). Decide whether to refuse that or release/shrink holds; FIRM holds are drawn down at pickup (§9).
- [S06 → S20] Demo timers need `make worker` running beside `make hub`; `make e2e` does not start it. The concurrency tests create a second database `<test db without _test>_conc_test` (needs CREATEDB, as `care_test` already does).
- [S06 → any] The conftest guard refuses a test DB whose name does not end in `_test` (e.g. `care_test_s06`); use names like `care_s06_test` for per-agent test databases.

## Known gaps
<!-- Things knowingly left incomplete, with the reason. -->
- S01: CI has not run on GitHub yet (no remote). `ci.yml` was validated with actionlint and its steps were run locally (`uv sync --locked`, ruff, mypy, pytest, `pnpm install --frozen-lockfile`, lint, typecheck, test).
- S02: CI postgres/redis service containers were validated with actionlint (via Docker), not on GitHub (still no remote).
- S02: The state-machine 409 is proven through a test-only route (`/api/v1/_test/transition`, mounted only in the test app); no S02 endpoint has a domain state machine.
- S01: Prettier skips the S00 kit prose (`docs/`, `.claude/`, `CLAUDE.md`) because those files aren't Prettier-formatted, and reformatting would rewrite the specs.
- S14: The manual hardware test (firmware/cold-box/README.md, "Manual hardware test") is pending until the ESP32 and DS18B20 arrive. Firmware is compile-verified only; the CI `firmware` job was validated with actionlint and run locally, not on GitHub.
- S03: The `client-drift` CI job was checked with actionlint and its steps were run locally, not on GitHub (no remote yet).
- S03: openapi-typescript 7.13 declares a peer of TypeScript ^5.x; with the pinned TS 6.0.3 pnpm warns, but codegen works and its output typechecks.
- S03: `@vitejs/plugin-react` is pinned `~6.1.1` because pnpm's minimum-release-age check rejected 6.1.2 (published the same day).
- S03: Light theme only; there are no dark-mode tokens.
- S08: Part 1 screens were verified with Vitest against a fake hub, plus a curl replay of every request shape they send (raw `text/csv` import, bodiless cancel/re-run, the 400 "Nothing to source" message, 409 on a repeat cancel) against a hub on a throwaway database. They were not clicked through in a browser: Chromium could not be downloaded here.
- S06: arq is pinned at 0.25.0 (Dec 2022): arq 0.26 requires redis-py < 6 and the hub uses redis-py 8.1. 0.25 was checked against redis-py 8 (cron ticks, `make worker`); revisit when arq supports redis 6+.
- S06: Accept re-checks quantity and shelf life against current stock (holds included) but not the other gates (freshness, authorization, deadline); those were checked by the match run that created the request.
- S06: A split decline or expiry supersedes the other sources' requests and releases their holds (§7 step 6 "every related hold"); the re-run may ask them again.

## Hardening checklist (S20)
<!-- Record the outcome of each check from S20. -->
