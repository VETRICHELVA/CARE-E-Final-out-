# Apps, AI and IoT

## Shared rules for all three apps
- Each app is its own Vite project with its own login, but all use `packages/ui` and `packages/api-client`.
- The header always shows the logged-in user, their organization and its type.
- Buttons appear only if the user has the capability (from `/auth/me`) and the resource's state allows it. The hub still checks every call.
- Live updates: subscribe to `/events/stream` and invalidate queries (see `api-and-events.md`).
- Every list has empty, loading and error states. Destructive or consequential actions ask for confirmation and offer an optional reason box.
- Times display in the user's local zone; quantities with units; money as ₹ with Indian digit grouping.
- Development ports: hospital-web 5173, supplier-web 5174, delivery-web 5175, hub 8000, ai-service 8100.

## hospital-web
| Screen | Route | Content | Section |
|---|---|---|---|
| Dashboard | / | Open shortages by status, incoming requests awaiting response, deliveries due today, expiry-risk count | S08, extended S18 |
| Inventory | /inventory | Batches table: product, batch, on hand, reserved, allocated, safety, quarantined, **transferable**, expiry, last verified; edit, verify, CSV import | S08 |
| Shortages | /shortages | List with status chips; "New shortage" form (product, qty required, local usable, required by, priority, min shelf life, notes); hub-computed shortfall is shown after save | S08 |
| Shortage detail | /shortages/:id | Status timeline; latest match run: eligible candidates ranked, rejected ones with reasons; source requests and their state; audit trail | S08, extended S12 |
| Recommendation | /shortages/:id (decision panel) | Type badge, lines, cost, ETA, explanation, alternatives; buttons worded by type (business-rules §13); reject or escalate with optional reason | S12 |
| Incoming requests | /requests | Requests addressed to this hospital: product, qty, deadline countdown; Accept (places hold) or Decline (optional reason) | S08 |
| Deliveries | /deliveries | Inbound shipments with live status, ETA, cold-chain badge | S12 |
| Receive | /deliveries/:id/receive | Expected, received, accepted, rejected, condition, inspection note (required if excursion) | S12 |
| Chat | panel on every page | Chat ordering and copilot (see AI) | S13, S17 |
| Forecasts and surplus | /forecasts | Predicted stock-outs, reorder suggestions, expiry-risk batches with "Offer to network" | S18 |

## supplier-web
| Screen | Route | Content | Section |
|---|---|---|---|
| Dashboard | / | New purchase orders, orders to dispatch, low-stock offers | S10 |
| Catalog and offers | /offers | Per product: price, lead time, available qty, last updated; inline edit | S10 |
| Purchase orders | /orders | List and detail; Acknowledge, Reject (reason), Mark dispatched | S10 |
| Network demand | /demand | Open shortages for this supplier's products, aggregated by product (no hospital identities) | S10 |

## delivery-web (responsive down to 360 px)
| Screen | Route | Content | Section |
|---|---|---|---|
| Dispatch board | / | Unassigned shipments: pickup, drop, deadline, qty, cold-chain flag; assign driver and vehicle | S11 |
| Shipment detail | /shipments/:id | Map with route (OSRM), ETA, status history, live position, cold-chain panel | S11, S15 |
| Route planner | /plan | Choose a driver and several shipments → optimized stop order with time windows | S16 |
| Fleet | /fleet | Drivers and vehicles; device assignment for cold boxes | S11, S14 |
| Driver jobs | /driver | For DRIVER role: my jobs, big status buttons (Picked up, In transit, Delivered), "Share location" toggle sending pings every 30 s | S11 |

Maps: Leaflet with OpenStreetMap tiles.

## AI service (`services/ai-service`)
- Calls the hub only through `/api/v1/ai/read/*` with a **read-only service token**. It has no database access and no write endpoints.
- The LLM provider and model are set by environment variables (`AI_PROVIDER`, `AI_MODEL`, `AI_API_KEY`). With no key, the service returns a clear "AI is not configured" response, so the rest of the system still works.
- Every response includes the tool calls it made, so the UI can show "Based on: match run #3, candidate Hospital D".

### Copilot (S13)
- Tools: `get_shortage(id)`, `get_match_run(shortage_id)`, `get_candidate(id)`, `get_recommendation(id)`, `get_shipment(id)`, `get_coldchain_events(shipment_id)`, `get_audit(entity, id)`.
- The system prompt requires: answer only from tool results; quote numbers exactly; if the data isn't there, say so; never suggest an action has been taken.
- The context sent with each question = the screen the user is on (e.g. shortage_id), plus the question.
- Test set: 15 questions with expected facts; a test fails if the answer contains a number not present in the tool results.

### Chat ordering (S17)
- Input: free text. Output: a **draft** `{product_id or candidates[], qty_required, required_by, priority, min_shelf_life_days, notes}` plus fields it could not fill.
- Product resolution: fuzzy match against the catalog (name, code, synonyms). If more than one product scores within 10% of the best → return `candidates` and ask the user to choose. Never pick silently.
- Relative dates ("by Friday", "in 72 hours") are resolved in the user's time zone and shown back for confirmation.
- The UI shows a confirmation card; only the user's click calls `POST /shortages` (as the user, with source=CHAT).
- Test set: 20 phrasings; at least 18 must produce a correct draft or a correct clarifying question.

### Forecasting (S18 — runs in the hub worker, not the AI service, so the AI service stays read-only)
- Daily consumption per hospital × product from ConsumptionRecord. Model: Holt-Winters exponential smoothing with weekly seasonality (statsmodels); fall back to a 28-day moving average when history is under 60 days.
- Outputs per product: 30-day forecast with interval, predicted stock-out date (when cumulative forecast exceeds usable stock), reorder suggestion (forecast lead-time demand + safety stock − usable stock).
- Expiry risk: a batch whose forecast usage before expiry is less than its on_hand minus safety stock → suggest a surplus post of the excess.
- Runs nightly as a job and on demand; results stored in Forecast.

## IoT
### Hardware
ESP32 DevKit, DS18B20 waterproof probe with a 4.7 kΩ pull-up on the data line, USB power bank, insulated box with gel packs (2–8 °C).

### Firmware (`firmware/cold-box`, Arduino framework via PlatformIO)
- Wi-Fi and MQTT settings come from `secrets.h` (git-ignored; `secrets.example.h` committed).
- Every 10 s, publish to `careE/devices/{device_id}/telemetry`: `{"device_id":"cb-01","ts":"2026-11-20T10:15:00Z","temp_c":4.31,"battery":82}`.
- Time from NTP. Buffer up to 60 readings in memory while offline and publish them on reconnect.
- Battery: percentage from an ADC reading if wired, otherwise omit the field.

### Ingest (`services/iot-ingest`)
- Subscribes to `careE/devices/+/telemetry`, validates each message, de-duplicates on (device_id, ts), and posts batches to the hub's `/internal/telemetry` every 2 s.
- The hub stores readings, updates Device.last_seen and battery, links readings to the device's assigned shipment, and runs the cold-chain rules (business-rules §11).

### Simulator
`scripts/simulate_telemetry.py --device cb-01 --profile normal|excursion|silent` publishes the same messages as the firmware, so every demo works without hardware.
