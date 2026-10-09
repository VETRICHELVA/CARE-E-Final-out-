# Domain model

Every table: `id` UUID PK, `created_at`, `updated_at` (UTC). Owned tables carry `org_id`. Money is integer paise.

## Identity
- **Organization**: name, type (HOSPITAL | SUPPLIER | LOGISTICS | PLATFORM), status (ACTIVE | SUSPENDED), lat, lng
- **Facility**: org_id, name, address, lat, lng, has_cold_storage
- **User**: email (unique), password_hash, full_name, org_id, is_active
- **Role**: name — STORE_MANAGER, REQUESTER, APPROVER, RECEIVER, SUPPLIER_DESK, DISPATCHER, DRIVER, ADMIN
- **Capability**: code, e.g. `shortage.create`, `recommendation.approve`, `source_request.respond`, `receipt.record`, `inventory.edit`, `po.respond`, `shipment.assign`, `shipment.update_status`, `audit.read`
- **UserRole**: user_id, role_id (a role maps to a fixed capability set in code)
- **RefreshToken**: user_id, family_id, token_hash, expires_at, revoked_at
- **ProductAuthorization**: org_id, product_id (which products an org may supply)

## Catalog
- **Product**: code (unique), name, category, unit, requires_cold_chain, temp_min_c, temp_max_c (nullable), default_min_shelf_life_days
- **SupplierOffer**: org_id (supplier), product_id, unit_price_paise, lead_time_hours, available_qty, updated_at

## Inventory
- **InventoryBatch**: org_id, facility_id, product_id, batch_no, on_hand, reserved, allocated, safety_stock, quarantined, expiry_date, unit_cost_paise, last_verified_at
- **VerificationEvent**: batch_id, user_id, method (MANUAL | SCAN), counted_qty, ts

## Demand
- **Shortage**: org_id, facility_id, product_id, qty_required, qty_local_usable, shortfall (computed by hub), required_by, priority (CRITICAL | ROUTINE), min_shelf_life_days, status, notes, parent_shortage_id (residuals), created_by, source (FORM | CHAT)
- **SurplusPost**: org_id, batch_id, product_id, qty, expiry_date, min_price_paise, status (OPEN | MATCHED | WITHDRAWN | EXPIRED)
- **Forecast**: org_id, product_id, date, predicted_qty, lower, upper, model_version
- **ConsumptionRecord**: org_id, product_id, date, qty (synthetic history for forecasting) — S18 adds `synthetic` (true for seeded rows; a reported row for the same day wins)
- **SurplusMatch** (S18): surplus_id, org_id, kind (SHORTAGE | FORECAST), shortage_id, stockout_date — one per post and matched org; the matched org then sees the post

## Matching
- **MatchRun**: shortage_id, run_no, triggered_by (CREATE | DECLINE | EXPIRY | MANUAL | RECOMMENDATION_EXPIRED | STOCK_CHANGE), ts
- **Candidate**: match_run_id, source_org_id, source_type (HOSPITAL | SUPPLIER), batch_ids, transferable_qty, offered_qty, gate_results (JSON: gate → pass/fail + reason), eligible, landed_cost_paise, eta_hours, reliability, rank
- **SourceRequest**: shortage_id, candidate_id, source_org_id, qty, status, sla_deadline, responded_by, responded_at, decline_reason, reason_source
- **Hold**: source_request_id, batch_id, qty, status (TENTATIVE | FIRM | RELEASED | CONSUMED), expires_at. Active (counted as `reserved`, business-rules §2) while TENTATIVE or FIRM. CONSUMED (S11): a FIRM hold drawn down at pickup together with the batch's on_hand (business-rules §8 Hold, §9; a short pickup draws down only what the batch records); unlike RELEASED it frees no stock and re-runs no shortage
- **Recommendation**: shortage_id, match_run_id (unique), type (TRANSFER | TRANSFER_SPLIT | BUY), lines (JSON: source, qty, cost, eta), alternatives (JSON), explanation, status (PENDING | APPROVED | REJECTED | ESCALATED | EXPIRED), expires_at, decided_by, decided_at, reason, reason_source
  - Each line (S09): candidate_id, source_org_id, source_org_name, source_type, qty, eta_hours, landed_cost_paise (stored for every source, shown to the requester for suppliers only), unit_price_paise (suppliers), shelf_life_days at delivery and source_request_id (hospitals). At most one PENDING or ESCALATED recommendation per shortage. `reason` is null when the decider typed none (`reason_source = SYSTEM`)

## Fulfillment
- **PurchaseOrder**: shortage_id, supplier_org_id, product_id, qty, unit_price_paise, status, eta
- **Vehicle**: org_id, reg_no (unique per org), has_cold_chain
- **Driver**: org_id, user_id (unique; the DRIVER user who signs in), phone, active
- **Shipment**: shortage_id, source_request_id or purchase_order_id, from_org_id, to_org_id, carrier_org_id (S11: the assigning org; null while CREATED), product_id, qty, requires_cold_chain, status, driver_id, vehicle_id, device_id, planned_eta (the plan's estimate at approval), eta (S11: computed from the route at assignment), route_distance_km, route_provider (OSRM | HAVERSINE), route_geometry (GeoJSON LineString), status_history (S11: [{from, to, at}]). S09 creates it CREATED; driver, vehicle and carrier are set together exactly when it is not CREATED
- **ShipmentLeg**: shipment_id, seq, stop_type (PICKUP | DROP), location (place name, lat, lng), planned_at, actual_at. S11 adds seq 1 PICKUP (the facility holding most of the held stock, or the supplier's location) and seq 2 DROP (the requesting facility) when the shipment is created; actual_at is set when the driver records PICKED_UP / DELIVERED
- **LocationPing**: shipment_id, lat, lng, ts (from the driver's phone). Shown only for the current assignment: a ping stored before it (e.g. the previous carrier's driver) is never returned
- **Receipt**: shipment_id (unique), shortage_id, expected (the shipment's qty), received, accepted, rejected, condition (GOOD | DAMAGED | TEMPERATURE_ISSUE), inspection_note, received_by, ts, batch_id (S12: the receiver's new InventoryBatch holding the accepted stock; null when nothing was accepted). Checked: received ≤ expected, accepted + rejected = received
- **Reconciliation**: shortage_id, shipment_id (unique), expected, accepted, discrepancy (= expected − accepted for that shipment), outcome (CONFIRMED | PARTIAL), residual_shortage_id. Written for every shipment of the shortage when the last receipt arrives; `outcome` and `residual_shortage_id` are the shortage's (PARTIAL exactly when a residual was opened)

## Trust
- **ReliabilityScore**: org_id (unique), acceptance_rate, median_response_minutes, response_speed, on_time_rate, discrepancy_rate, score (0–100), computed_at. A component is null while the org has no history for it
- **CreditLedger** (append-only): org_id, delta, reason, shortage_id, ts; one row per source org and shortage

## IoT
- **Device**: org_id (owning org, e.g. the logistics company), device_id (string, unique), type, assigned_shipment_id (only a shipment its own org carries; cleared when assign or unassign changes the carrier), battery_level, last_seen
- **SensorReading**: device_id, ts, temp_c, battery, shipment_id (the device's assigned shipment when stored, only if it was ASSIGNED, PICKED_UP or IN_TRANSIT); unique (device_id, ts)
- **ColdChainEvent**: shipment_id, device_id, type (EXCURSION | DEVICE_SILENT | RECOVERED), threshold, observed_value, severity, ts

## Records
- **AuditLog** (append-only): actor_id (nullable for system), org_id, entity, entity_id, action (e.g. `shortage.created`), before (JSON), after (JSON), reason, reason_source (USER | SYSTEM), ts
- **Notification**: user_id, type, payload, read_at
- **EventOutbox**: event_type, org_ids, payload (the envelope), created_at, published_at, seq (publish order; the SSE event id)
- **WebhookSubscription**: org_id, url, secret, event_types
- **WebhookDelivery**: subscription_id, event_id, attempt, status (PENDING | DELIVERED | RETRY_SCHEDULED | FAILED), next_attempt_at, response_code — one row per attempt; a failed attempt is RETRY_SCHEDULED (a PENDING row for the next attempt follows) or FAILED (no more attempts)

## Key relationships
- Shortage 1–N MatchRun 1–N Candidate; a MatchRun has at most one current Recommendation.
- Top Candidates get SourceRequests (with Holds); confirmed ones feed the Recommendation.
- Approval creates one Shipment per confirmed source, or a PurchaseOrder (which then gets a Shipment).
- Shipment 1–1 Receipt 1–1 Reconciliation; a PARTIAL reconciliation creates a residual Shortage.
