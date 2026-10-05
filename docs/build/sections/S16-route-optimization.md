# S16 — Route optimization

**Milestone:** M3 · **Depends on:** S11 · **Workstream:** IoT/Logistics · **Can run alongside:** S15, S17, S18

## Read first
- `docs/specs/apps-ai-iot.md` — delivery-web Route planner
- `docs/specs/api-and-events.md` — S16 row
- `docs/specs/business-rules.md` — §4, §8 Shipment table

## Goal
A dispatcher picks a driver and several shipments, and gets a stop order that keeps every pickup before its drop, meets every deadline, and respects cold-chain transit limits. Any shipment that can't fit is reported with a reason, never silently dropped.

## Build
- **Business rule addition** (ask first, then update `business-rules.md` §4 and `config.py`): cold-chain shipments have a maximum ride time of `COLD_CHAIN_MAX_TRANSIT_HOURS` (default 4) between pickup and drop.
- **Hub `app/routing/optimizer.py`:**
  - Get a duration matrix from OSRM's table service (haversine fallback).
  - Solve a pickup-and-delivery problem with time windows using Google OR-Tools: pickup window from now, drop window ending at the shortage's `required_by`, and a maximum ride time for cold-chain pairs.
  - Search limit 5 s.
- **`POST /routes/optimize`:** `{driver_id, shipment_ids}` → `{stops: [{shipment_id, type, eta}], infeasible: [{shipment_id, reason}]}`.
- **`POST /routes/apply`:** writes ShipmentLegs with `planned_at` and assigns the driver to the feasible shipments. Add it to `api-and-events.md`.
- **delivery-web Route planner:** pick a driver and shipments; show the stop list and the route on a map; Apply.

## Out of scope
Multi-vehicle fleet optimization and live re-planning.

## Acceptance criteria
- [ ] With a fixed test matrix, 3 shipments for one driver give a plan that meets all windows, with each pickup before its drop (test).
- [ ] A shipment whose deadline can't be met appears in `infeasible` with a reason such as "Cannot reach Hospital C before 14:00".
- [ ] A cold-chain pair whose ride time would exceed the limit is reported infeasible.
- [ ] 10 shipments solve in under 5 s.
- [ ] Applying a plan writes legs and assignments, with audit rows.

## Verify
```
make test-hub && make test-web
```
