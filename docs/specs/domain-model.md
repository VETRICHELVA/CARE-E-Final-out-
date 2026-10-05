# Domain model

Every table: `id` UUID PK, `created_at`, `updated_at` (UTC). Owned tables carry `org_id`. Money is integer paise.

## Identity
- **Organization**: name, type (HOSPITAL | SUPPLIER | LOGISTICS | PLATFORM), status (ACTIVE | SUSPENDED), lat, lng
- **Facility**: org_id, name, address, lat, lng, has_cold_storage
- **User**: email (unique), password_hash, full_name, org_id, is_active
- **Role**: name — STORE_MANAGER, REQUESTER, APPROVER, RECEIVER, SUPPLIER_DESK, DISPATCHER, DRIVER, ADMIN
- **Capability**: code, e.g. `shortage.create`, `recommendation.approve`, `source_request.respond`, `receipt.record`, `inventory.edit`, `po.respond`, `shipment.assign`, `shipment.update_status`, `audit.read`
- **UserRole**: user_id, role_id (a role maps to a fixed capability set in code)
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
- **ConsumptionRecord**: org_id, product_id, date, qty (synthetic history for forecasting)

## Matching
- **MatchRun**: shortage_id, run_no, triggered_by (CREATE | DECLINE | EXPIRY | MANUAL | RECOMMENDATION_EXPIRED), ts
- **Candidate**: match_run_id, source_org_id, source_type (HOSPITAL | SUPPLIER), batch_ids, transferable_qty, offered_qty, gate_results (JSON: gate → pass/fail + reason), eligible, landed_cost_paise, eta_hours, reliability, rank
- **SourceRequest**: shortage_id, candidate_id, source_org_id, qty, status, sla_deadline, responded_by, responded_at, decline_reason, reason_source
- **Hold**: source_request_id, batch_id, qty, status (TENTATIVE | FIRM | RELEASED), expires_at
- **Recommendation**: shortage_id, match_run_id, type (TRANSFER | TRANSFER_SPLIT | BUY), lines (JSON: source, qty, cost, eta), alternatives (JSON), explanation, status (PENDING | APPROVED | REJECTED | ESCALATED | EXPIRED), expires_at, decided_by, decided_at, reason, reason_source

## Fulfillment
- **PurchaseOrder**: shortage_id, supplier_org_id, product_id, qty, unit_price_paise, status, eta
- **Vehicle**: org_id, reg_no, has_cold_chain
- **Driver**: org_id, user_id, phone, active
- **Shipment**: shortage_id, source_request_id or purchase_order_id, from_org_id, to_org_id, product_id, qty, requires_cold_chain, status, driver_id, vehicle_id, device_id, planned_eta, route_geometry
- **ShipmentLeg**: shipment_id, seq, stop_type (PICKUP | DROP), location, planned_at, actual_at
- **LocationPing**: shipment_id, lat, lng, ts (from the driver's phone)
- **Receipt**: shipment_id, expected, received, accepted, rejected, condition (GOOD | DAMAGED | TEMPERATURE_ISSUE), inspection_note, received_by, ts
- **Reconciliation**: shortage_id, shipment_id, expected, accepted, discrepancy, outcome (CONFIRMED | PARTIAL), residual_shortage_id

## Trust
- **ReliabilityScore**: org_id, acceptance_rate, median_response_minutes, on_time_rate, discrepancy_rate, score (0–100), computed_at
- **CreditLedger**: org_id, delta, reason, shortage_id, ts

## IoT
- **Device**: device_id (string, unique), type, assigned_shipment_id, battery_level, last_seen
- **SensorReading**: device_id, ts, temp_c, battery; unique (device_id, ts)
- **ColdChainEvent**: shipment_id, device_id, type (EXCURSION | DEVICE_SILENT | RECOVERED), threshold, observed_value, severity, ts

## Records
- **AuditLog** (append-only): actor_id (nullable for system), org_id, entity, entity_id, action (e.g. `shortage.created`), before (JSON), after (JSON), reason, reason_source (USER | SYSTEM), ts
- **Notification**: user_id, type, payload, read_at
- **EventOutbox**: event_type, payload, created_at, published_at
- **WebhookSubscription**: org_id, url, secret, event_types
- **WebhookDelivery**: subscription_id, event_id, attempt, status, next_attempt_at, response_code

## Key relationships
- Shortage 1–N MatchRun 1–N Candidate; a MatchRun has at most one current Recommendation.
- Top Candidates get SourceRequests (with Holds); confirmed ones feed the Recommendation.
- Approval creates one Shipment per confirmed source, or a PurchaseOrder (which then gets a Shipment).
- Shipment 1–1 Receipt 1–1 Reconciliation; a PARTIAL reconciliation creates a residual Shortage.
