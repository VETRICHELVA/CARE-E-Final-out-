# S11 — Shipments, routing and the delivery app

**Milestone:** M2 · **Depends on:** S09, S03, S07 · **Workstream:** Backend + Frontend (split across two sessions if needed: hub first, then delivery-web) · **Can run alongside:** S10, S14

## Read first
- `docs/specs/business-rules.md` — §4 (ETA and cost), §8 Shipment table, §9 last bullet (inventory moves at pickup)
- `docs/specs/domain-model.md` — Fulfillment (Vehicle, Driver, Shipment, ShipmentLeg, LocationPing)
- `docs/specs/apps-ai-iot.md` — delivery-web table
- `docs/specs/api-and-events.md` — S11 rows

## Goal
The logistics partner sees every shipment that needs moving, assigns a driver and vehicle with a real road route and ETA, and the driver moves the shipment through its states from a phone, with inventory updated at pickup.

## Build
- **Hub models:** Vehicle, Driver, ShipmentLeg, LocationPing; extend Shipment.
- **Assign and unassign:**
  - A cold-chain shipment requires a cold-chain vehicle; otherwise 400 with a reason.
  - On assign, compute the route and ETA and store the geometry.
- **Status:** only the assigned driver may move a shipment, following the state machine.
- **At PICKED_UP:** reduce the source's `on_hand` and consume its FIRM hold (transfers); write audit rows.
- **Location pings:** stored, and emitted as `shipment.location`.
- **`OSRMProvider`** implements `RoutingProvider` from S05 (route + table), falling back to `HaversineProvider` on error or timeout (2 s).
- **OSRM setup:** `infra/osrm/prepare.sh` downloads an OpenStreetMap extract for the demo city's region and runs osrm-extract, osrm-partition and osrm-customize. Document it in `infra/osrm/README.md`; it runs under the compose profile `routing`.
- **delivery-web:**
  - Dispatch board.
  - Shipment detail with a Leaflet map, route, ETA, status history and live position.
  - Fleet (drivers, vehicles).
  - Driver jobs view for DRIVER users: large status buttons, a "Share location" toggle sending a ping every 30 s.
  - Mobile-first down to 360 px.

## Out of scope
Multi-stop optimization (S16), cold-chain panels (S15), device assignment (S14).

## Acceptance criteria
- [ ] A driver who is not assigned, or is from another org, gets 403 on a status update.
- [ ] Skipping a state (CREATED → PICKED_UP) returns 409.
- [ ] Pickup test: B's `on_hand` drops by the shipped qty and the FIRM hold is consumed; audit rows exist.
- [ ] OSRM unavailable → the ETA still comes back through haversine (test with a failing fake provider).
- [ ] The driver view works at 360 px (Playwright viewport test).
- [ ] Assigning a non-cold vehicle to a cold-chain shipment is refused with a clear reason.

## Verify
```
make test-hub && make test-web && make e2e
docker compose -f infra/docker-compose.yml --profile routing up -d osrm   # after prepare.sh
```
