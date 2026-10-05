# S20 — Demo scenarios, end-to-end tests and hardening

**Milestone:** M4 · **Depends on:** S01–S19 (anything cut stays cut; note it in PROGRESS.md) · **Workstream:** Everyone · **Can run alongside:** nothing; no new features from here

## Read first
- `docs/specs/demo-scenarios.md` — all of it
- `CLAUDE.md` — Non-negotiable rules
- `docs/build/PROGRESS.md` — every Follow-up and known gap

## Goal
A fresh clone becomes a working, seeded demo in under 10 minutes, all three scenarios pass end to end repeatedly, and the obvious security and performance gaps are closed.

## Build
- **`make seed`:** a complete, idempotent seed of every org, user, product, authorization, batch, offer, vehicle, driver, device and consumption history in `demo-scenarios.md`, with times relative to now.
- **`make demo-reset`:** wipe the database and seed again.
- **Playwright end-to-end tests**, one per scenario, each driving every involved app with separate browser contexts. Scenario 2 runs the telemetry simulator.
- **Admin metrics:**
  - `GET /metrics/network`: median time from shortage to confirmed source, transfer vs purchase share, procurement cost avoided, units saved from expiry, cold-chain compliance.
  - An `/admin` page in hospital-web, visible only to PLATFORM users.
- **Hardening checklist** (record the outcome of each in PROGRESS.md):
  - CORS limited to the three app origins; auth rate limits; no secrets committed (`git grep` for keys).
  - `pip-audit` and `pnpm audit` clean, or each finding justified.
  - Performance test: matching across 10 orgs and 40 products under 2 s.
  - Every list screen has loading, empty and error states; check keyboard focus on dialogs.
  - Run `/check-section` on S05, S06, S09 and S12 one final time; fix anything FAIL.
- **Docs:**
  - Root `README.md`: what CARE-E is, a 10-minute quickstart, architecture overview, links to specs.
  - `docs/demo-runbook.md`: a click-by-click script for all three scenarios with expected screens, plus a fallback plan (simulator, recorded video).

## Out of scope
New features.

## Acceptance criteria
- [ ] Fresh clone → `make up && make migrate && make seed` → all three apps usable in under 10 minutes, following the README only.
- [ ] All three end-to-end scenario tests pass 3 times in a row (`make e2e` ×3).
- [ ] The matching performance test passes (< 2 s).
- [ ] Hardening checklist complete in PROGRESS.md.
- [ ] Every number in `demo-scenarios.md` appears as stated in the running demo.

## Verify
```
make demo-reset && make test && for i in 1 2 3; do make e2e || break; done
```
