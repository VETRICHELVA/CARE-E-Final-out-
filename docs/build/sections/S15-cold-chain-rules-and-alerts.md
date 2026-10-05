# S15 — Cold-chain rules and alerts

**Milestone:** M3 · **Depends on:** S14, S12 · **Workstream:** IoT + Frontend · **Can run alongside:** S16, S17

## Read first
- `docs/specs/business-rules.md` — §9 (inspection note), §11 Cold chain
- `docs/specs/domain-model.md` — IoT (ColdChainEvent)
- `docs/specs/apps-ai-iot.md` — delivery-web shipment detail; hospital-web Deliveries and Receive
- `docs/specs/demo-scenarios.md` — Scenario 2

## Goal
The hub turns readings into evidence-based cold-chain events. Both the carrier and the receiving hospital see excursions live, and a hospital cannot accept affected stock without recording an inspection.

## Build
- **Hub:**
  - ColdChainEvent model.
  - Rule evaluation on each ingested batch, per shipment, in timestamp order: EXCURSION after 2 consecutive out-of-range readings; RECOVERED after 2 consecutive in-range readings following an excursion.
  - A worker job every 30 s raises DEVICE_SILENT when an IN_TRANSIT shipment's device has been silent for 2 minutes.
  - Emit the `coldchain.*` events.
  - Implement `has_open_excursion(shipment)`: true if any EXCURSION exists for the shipment, even if recovered.
  - `GET /shipments/{id}/coldchain`.
- **Shared UI** (`packages/ui`): a `ColdChainPanel` with a live temperature line chart, the allowed band shaded, an event list, battery and last-seen; plus alert toasts.
- **delivery-web:** the panel on shipment detail; a cold-chain badge on the dispatch board.
- **hospital-web:**
  - The panel on the delivery detail, with an alert toast for excursions on inbound shipments.
  - The receive screen shows a red notice and requires an inspection note when `has_open_excursion` is true.

## Out of scope
Humidity, door sensors, and GPS from the device.

## Acceptance criteria
- [ ] Scenario 2 with `--profile excursion`: the alert appears in both apps within 30 s, and RECOVERED follows.
- [ ] A single out-of-range spike does not create an EXCURSION (test).
- [ ] `--profile silent` on an IN_TRANSIT shipment → DEVICE_SILENT within about 2.5 min.
- [ ] Accepting without an inspection note after an excursion → 400; with a note → accepted, and the note appears in the audit.
- [ ] Audit rows record each cold-chain event as SYSTEM with the observed value and threshold.

## Verify
```
make test-hub && make test-web && make e2e
python scripts/simulate_telemetry.py --device cb-01 --profile excursion
```
