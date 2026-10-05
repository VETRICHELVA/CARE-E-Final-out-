# S18 — Forecasting and expiry surplus

**Milestone:** M3 · **Depends on:** S12, S07 · **Workstream:** AI/ML (statistics in the hub worker) + Frontend · **Can run alongside:** S16, S17, S19

## Read first
- `docs/specs/apps-ai-iot.md` — Forecasting (note: runs in the hub worker); hospital-web Forecasts row
- `docs/specs/domain-model.md` — Demand (SurplusPost, Forecast, ConsumptionRecord)
- `docs/specs/business-rules.md` — §5 (near-expiry tiebreak)
- `docs/specs/demo-scenarios.md` — Scenario 3; Synthetic consumption history
- `docs/specs/api-and-events.md` — S18 rows; `surplus.matched`

## Goal
Hospitals see stock-outs coming and are nudged to offer near-expiry excess to the network before it's wasted, and the two sides are matched automatically.

## Build
- **Models:** ConsumptionRecord, Forecast, SurplusPost.
- **`scripts/seed/consumption.py`:** a deterministic synthetic history per the spec. Scenario 3's two series must produce the stated numbers.
- **Hub `app/forecasting/`** (statsmodels):
  - Holt-Winters with weekly seasonality, falling back to a 28-day moving average under 60 days of history.
  - Derives predicted stock-out date, reorder suggestion and expiry-risk excess per batch.
  - Runs as a nightly worker job and on demand (`POST /forecasts/run`, platform admin or `inventory.edit`).
- **Surplus:**
  - Create (from an expiry-risk suggestion or manually), list, withdraw; expires automatically at batch expiry.
  - A surplus post's quantity is offered only up to the batch's current transferable.
  - **Matching:** on post creation and nightly, match open surplus to open shortages and to forecast stock-outs within 14 days for the same product. Emit `surplus.matched` to both orgs.
- **hospital-web:** the Forecasts screen (stock-out list with dates, reorder suggestions, expiry-risk batches with "Offer to network"), plus dashboard cards.

## Out of scope
Forecast accuracy tuning beyond the spec, and price negotiation for surplus.

## Acceptance criteria
- [ ] Scenario 3 test: B's expiry-risk excess = 300 ± 5; E's predicted stock-out = 4 ± 1 days.
- [ ] `surplus.matched` reaches Hospital E's event stream; a withdrawn surplus is never matched.
- [ ] Forecast runs are reproducible: same history → same numbers (test).
- [ ] In a ROUTINE match, Hospital B's near-expiry batch ranks first against an equal-cost source.

## Verify
```
(cd services/hub-api && uv run python ../../scripts/seed/consumption.py) && make test-hub && make test-web
```
