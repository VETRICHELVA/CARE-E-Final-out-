# S06 — Source requests, holds and timers

**Milestone:** M1 · **Depends on:** S05 · **Workstream:** Backend · **Can run alongside:** S03, S08 (UI can start against the contract)

## Read first
- `docs/specs/business-rules.md` — §2 (holds count as reserved), §6, §7 steps 2, 3 and 6, §8 (SourceRequest table), §10
- `docs/specs/domain-model.md` — Matching (SourceRequest, Hold)
- `docs/specs/api-and-events.md` — S06 rows

## Goal
A planned transfer turns into source requests with deadlines. A source's accept places row-locked tentative holds; declines, expiries and races release them and re-run matching automatically, even across a restart.

## Build
- **Models:** SourceRequest, Hold.
- After a match run with a TRANSFER or TRANSFER_SPLIT plan, create one SourceRequest per planned source with `sla_deadline`. (CRITICAL parallel requests come in S19; for now CRITICAL uses the plan with its shorter deadlines.)
- **Accept:**
  - In one transaction, `SELECT … FOR UPDATE` the source's qualifying batches.
  - Recompute transferable including existing holds.
  - Create TENTATIVE holds, earliest qualifying expiry first, and set the hold expiry.
  - Move the request to TENTATIVE_HOLD.
  - If the stock is no longer enough, return 409 `conflict`, expire the request with SYSTEM reason "Stock changed before acceptance.", and re-run matching.
- **Decline:** optional reason, then release and re-run with the org excluded for this shortage.
- **Timers:**
  - An arq worker (`make worker`; add it to `CLAUDE.md`) with a job every 30 s that expires overdue REQUESTED and TENTATIVE_HOLD requests.
  - The job is idempotent and safe if two workers run.
- **Single release path:** `release_and_rematch(shortage, reason)`, used by every non-CONFIRMED exit.
- Count active holds as reserved in every transferable calculation.
- A hook `on_sources_ready(shortage)`, called when every request in the plan is TENTATIVE_HOLD. Leave it as a no-op with a TODO for S09.
- Audit every transition. Timer actions use SYSTEM reasons such as "Response deadline passed."

## Out of scope
Recommendations and approval (S09); events (S07); screens (S08).

## Acceptance criteria
- [ ] Concurrency test: two accepts competing for the same batch at once → exactly one succeeds.
- [ ] Frozen-clock test: a CRITICAL request expires after 15 min; its holds are released, a new MatchRun exists, and the audit reason is "Response deadline passed." with SYSTEM.
- [ ] A decline without a reason → audit `reason_source=SYSTEM`, "No reason was entered."
- [ ] Scenario 1 step 3: B declines → the re-run excludes B → the plan becomes BUY from Supplier Y.
- [ ] Deadlines live in the database: restarting the worker does not lose or double-process them.
- [ ] Only the source org can accept or decline (403 for anyone else).

## Verify
```
make test-hub && make lint && make client
make worker   # in another terminal. For a quick manual check, set SLA_CRITICAL_RESPONSE_MINUTES=1 in .env
              # (config values in app/domain/config.py must be overridable by environment variables)
```
