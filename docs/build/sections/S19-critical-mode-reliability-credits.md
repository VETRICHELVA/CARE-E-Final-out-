# S19 — Critical mode, reliability and credits

**Milestone:** M3 · **Depends on:** S09, S12 · **Workstream:** Backend · **Can run alongside:** S16, S17, S18

## Read first
- `docs/specs/business-rules.md` — §5 (ranking uses reliability), §6, §7 step 2 (CRITICAL), §8 SourceRequest (SUPERSEDED), §12
- `docs/specs/domain-model.md` — Trust (ReliabilityScore, CreditLedger)
- `docs/specs/api-and-events.md` — S19 row

## Goal
Critical shortages don't wait on one slow hospital, and sources that reliably say yes and deliver accurately rise in the rankings.

## Build
- **CRITICAL parallel requests:**
  - When the plan is TRANSFER and the shortage is CRITICAL, send requests to up to 3 eligible single-source candidates at once.
  - The first accept wins under the row locks from S06.
  - In the same transaction, every other open request for that shortage becomes SUPERSEDED, its holds are released, and the audit reason is SYSTEM "Another source confirmed first."
  - The losing hospitals' apps show "No longer needed".
- **ReliabilityScore:**
  - Computed per §12 from SourceRequest, Shipment and Reconciliation history.
  - Recomputed nightly and after each reconciliation; orgs without history score 70.
  - Ranking reads the stored score, never computes it inline.
- **CreditLedger:** +1 per 10 accepted units of a transfer, written at reconciliation and attributed to the source org.
- `GET /orgs/{id}/reliability`.
- **UI:** a reliability badge on candidate rows (hospital-web) and on the org's own dashboard, with a tooltip of the four components; credits on the dashboard.

## Out of scope
Spending credits.

## Acceptance criteria
- [ ] Race test: 3 parallel requests and 3 simultaneous accepts → exactly 1 TENTATIVE_HOLD and 2 SUPERSEDED, with no orphan holds.
- [ ] A ROUTINE shortage still sends one request at a time.
- [ ] Score formula unit tests, including the no-history default of 70 and clamping to 0–100.
- [ ] Credits are written only for reconciled accepted quantity, never for shipped-but-rejected units.
- [ ] Ranking changes when reliability differs between two otherwise equal sources (test).

## Verify
```
make test-hub && make test-web
```
