# S05 — Shortages and the matching engine

**Milestone:** M1 · **Depends on:** S04 · **Workstream:** Backend · **Can run alongside:** S03, S14

## Read first
- `docs/specs/business-rules.md` — §1, §3, §4, §5, §8 (Shortage table)
- `docs/specs/domain-model.md` — Demand (Shortage), Matching (MatchRun, Candidate)
- `docs/specs/demo-scenarios.md` — Scenario 1 table
- `docs/specs/api-and-events.md` — S05 rows

## Goal
Creating a shortage runs a deterministic match: every hospital and supplier is checked against every gate, eligible sources are ranked, rejected ones carry plain-language reasons, and a planned resolution (TRANSFER, TRANSFER_SPLIT or BUY) is stored.

## Build
- **Models:** Shortage, MatchRun (with `planned_resolution` JSON and `excluded_org_ids`), Candidate.
- **`app/domain/config.py`:** every tunable from the business rules (time limits, rates, NEAR_EXPIRY_DAYS, MAX_SPLIT_SOURCES, default reliability 70).
- **`app/domain/gates.py`:** one pure function per gate, each returning `GateResult(passed, reason)` with the exact wording style of §3.
- **`app/domain/costing.py`:** distance through a `RoutingProvider` interface (`HaversineProvider` now; S11 adds OSRM), transport ETA, transport cost, landed cost.
- **`app/domain/ranking.py`** and **`app/domain/resolution.py`:** CRITICAL and ROUTINE ordering, greedy split (at most 3 sources), and the BUY fallback always computed as an alternative.
- **Service layer:**
  - `create_shortage` computes the shortfall, OPEN → MATCHING, then runs a match.
  - `run_match(shortage, trigger)` loads candidate data with only the fields matching needs, applies gates, ranks, and stores the MatchRun.
  - `cancel_shortage`.
- **Endpoints:** S05 rows. The latest match run shows eligible candidates ranked and rejected ones with every failed gate reason.
- Audit every shortage transition and match run.

## Out of scope
Sending source requests and holds (S06); recommendations (S09). In this section, a match ends with a stored `planned_resolution`.

## Acceptance criteria
- [ ] A Scenario 1 fixture test gives:
  - B eligible and ranked first.
  - C rejected "Only 100 transferable; 850 needed".
  - D rejected "Expires in 12 days; 30 required".
  - E rejected for authorization.
  - X and Y eligible; Y above X for CRITICAL; X above Y for ROUTINE.
- [ ] With B excluded and hospitals holding 500 and 350 transferable, the plan is TRANSFER_SPLIT using both; it never uses more than 3 sources.
- [ ] The shortfall ignores any client-sent value.
- [ ] Every gate has a pass test and a fail test; ranking has tests for each tiebreak.
- [ ] Cancelling a shortage is allowed only from OPEN, MATCHING or AWAITING_DECISION; otherwise 409.
- [ ] A Hospital B user cannot read Hospital A's shortage (403).

## Verify
```
make test-hub && make lint && make client
```
