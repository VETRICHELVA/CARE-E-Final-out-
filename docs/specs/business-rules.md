# Business rules

All rules run deterministically in the hub (`services/hub-api/app/domain/`). AI may explain them but never applies or overrides them. Tunable numbers live in one config module (`app/domain/config.py`), never as scattered literals.

## 1. Shortfall
```
shortfall = max(0, qty_required − qty_local_usable)
```
Computed by the hub on create. Any value the client sends is ignored. A shortfall of 0 (local usable stock covers the requirement) is rejected with 400 `validation`: no shortage is stored and no match runs.

## 2. Transferable quantity (per batch)
```
transferable = max(0, on_hand − reserved − allocated − safety_stock − quarantined)
```
- A batch whose `expiry_date` is today or earlier counts as fully expired: transferable = 0.
- A batch counts toward a shortage only if `days_to_expiry_at_delivery ≥ shortage.min_shelf_life_days`, where `days_to_expiry_at_delivery = (expiry_date − estimated_arrival_date).days`.
- Active holds on a batch count as `reserved` for every other shortage. A hold is active while TENTATIVE or FIRM; a RELEASED hold frees its stock, and a CONSUMED hold (drawn down at pickup together with `on_hand`, §9) no longer counts because the stock has left the batch.
- Source-level transferable = the sum over that source's qualifying batches of the same product.
- "Today" for expiry is the UTC date.
- Verifying a batch records a VerificationEvent and sets `last_verified_at`. If `counted_qty` differs from `on_hand`, the count replaces `on_hand` (audited, before and after).
- Who may write: inventory batches belong to HOSPITAL orgs, so only a hospital user with `inventory.edit` may create, edit, import or verify them. Supplier offers belong to SUPPLIER orgs, so only a supplier user with `po.respond` may write them. Any other org gets 403, even as ADMIN.

## 3. Eligibility gates
A candidate must pass every gate. Store each gate's result and a plain-language reason for each failure.

| Gate | Hospital source | Supplier source | Example failure reason |
|---|---|---|---|
| product | Batch product = shortage product | Offer product = shortage product | "Different product specification" |
| quantity | transferable ≥ shortfall (single); > 0 (split candidate) | available_qty ≥ shortfall | "Only 100 transferable; 850 needed" |
| shelf_life | §2 shelf-life rule | Assumed to pass (new stock) | "Expires in 12 days; 30 required" |
| authorization | Org ACTIVE and has ProductAuthorization | Same | "Not authorized to supply this product" |
| freshness | Per batch: last_verified_at within 24 h (CRITICAL) or 7 days (ROUTINE). An unverified or stale batch adds nothing to the source's quantity; the gate fails only when none of the batches that would count is fresh (the reason names the most recent count) | Offer updated within 7 days | "Stock last verified 9 days ago" |
| deadline | now + eta ≤ required_by | now + lead time + transport eta ≤ required_by | "Arrives 6 h after the deadline" |
| cold_chain | If product requires it: facility has_cold_storage AND a cold-chain vehicle exists | Same for vehicle | "No cold-chain transport available" |

## 4. Cost and ETA
- Distance: OSRM route distance once S11 adds routing; until then, and whenever OSRM is down, haversine × 1.3.
- Transport ETA (hours) = distance_km ÷ 40 + 1 (handover allowance).
- Transport cost = distance_km × `TRANSPORT_RATE_PAISE_PER_KM` (default 2,500 = ₹25/km).
- Hospital landed cost = qty × batch unit_cost_paise + transport + handling fee (`HANDLING_FEE_PCT`, default 2% of item value).
- Supplier landed cost = qty × unit_price_paise + transport.
- Supplier ETA = lead_time_hours + transport ETA.
- Cold-chain maximum ride time (S16): a cold-chain shipment may spend at most `COLD_CHAIN_MAX_TRANSIT_HOURS` (default 4) between pickup and drop, counted from arriving at its pickup to arriving at its drop (so the handover allowance and every stop in between count). The route planner reports a shipment that cannot meet it as infeasible.

## 5. Ranking (eligible candidates only)
- **CRITICAL:** earliest ETA → highest reliability score → lowest landed cost.
- **ROUTINE:** lowest landed cost → near-expiry first (batch expiring within `NEAR_EXPIRY_DAYS`, default 90) → highest reliability.
- "Landed cost" in ranking is **per unit**: the landed cost of what the source would supply, min(qty, shortfall), divided by that qty. Ranking on the total would put small sources first only because they supply less, and the greedy split would miss larger sources that cover the shortfall.
- A hospital source's landed cost is used for ranking but never shown to the requester's org (it would reveal that hospital's unit cost; org isolation). Match runs show landed cost for supplier sources only.
- An org with no history has reliability 70.
- **Resolution choice:**
  1. If a single hospital source covers the shortfall → TRANSFER from the top-ranked one.
  2. Else, if 2–3 hospital sources together cover it → TRANSFER_SPLIT, taking the greedy top-ranked sources until covered (max `MAX_SPLIT_SOURCES` = 3).
  3. Else → BUY from the top-ranked eligible supplier.
- The best BUY option is always computed and stored as an alternative, so the fallback is ready.
- If nothing is eligible: no recommendation; the shortage stays MATCHING with reason "No eligible source", and is re-run when inventory or offers change, or when tentative holds on that product are released (a cancel, decline or expiry frees held stock). Released holds are picked up by the worker within one timer tick, after the release commits; the run is `triggered_by = STOCK_CHANGE` with the cause "Held stock for this product was released."

## 6. Time limits
| Limit | CRITICAL | ROUTINE |
|---|---|---|
| Source must respond to a request (REQUESTED → accepted or declined) | 15 min | 4 h |
| Tentative hold before the requester decides | 30 min | 24 h |
| Recommendation validity | 30 min | 24 h |

Timers are stored as deadlines in the database and enforced by arq jobs, so they survive restarts.

## 7. The flow
1. A shortage is created (OPEN). The hub computes the shortfall and starts a MatchRun (→ MATCHING).
2. If the chosen resolution is TRANSFER or TRANSFER_SPLIT, the hub sends a SourceRequest to each chosen source.
   - ROUTINE: one source at a time (the split sources together).
   - CRITICAL (from S19): up to 3 single-source candidates in parallel; the first to accept wins, and the rest become SUPERSEDED.
3. A source **accepts** → its batches get TENTATIVE holds (row-locked: `SELECT … FOR UPDATE`) → SourceRequest TENTATIVE_HOLD.
4. When all requests for the chosen resolution are in TENTATIVE_HOLD, or immediately for BUY, the hub creates a Recommendation (→ AWAITING_DECISION).
5. The requester **approves**:
   - Transfer: holds become FIRM, requests become CONFIRMED, one Shipment per source (→ IN_FULFILLMENT).
   - Buy: a PurchaseOrder is created (→ IN_FULFILLMENT).
6. A **decline, an expiry, a rejection or a supplier PO rejection** releases every related hold and starts a new MatchRun. A source that said no, or was said no to, is excluded from this shortage's later runs: **declined → excluded; recommendation rejected → its sources excluded; PO rejected → that supplier excluded; expired → not excluded** (a source that missed its response deadline can be asked again). When the requester rejects a recommendation, the re-run leaves out every source of the rejected plan: its hospital lines (TRANSFER, TRANSFER_SPLIT) or its supplier (BUY). The alternatives listed with it are not excluded. A residual shortage inherits these exclusions (§9).
7. Receipt and reconciliation close it (§9).

## 8. State machines
Any transition not listed returns 409 `invalid_transition`.

**Shortage**
| From | To | Trigger |
|---|---|---|
| DRAFT | OPEN | Requester confirms a chat draft |
| OPEN | MATCHING | Match run starts (automatic) |
| MATCHING | AWAITING_DECISION | Recommendation created |
| AWAITING_DECISION | MATCHING | Recommendation rejected or expired, or a hold expired |
| AWAITING_DECISION | IN_FULFILLMENT | Recommendation approved |
| IN_FULFILLMENT | MATCHING | PO rejected by supplier |
| IN_FULFILLMENT | RECEIVED | All shipments have receipts |
| RECEIVED | RESOLVED | Accepted = shortfall |
| RECEIVED | PARTIALLY_RESOLVED | Accepted < shortfall (residual created) |
| OPEN, MATCHING, AWAITING_DECISION | CANCELLED | Requester cancels (releases holds) |
| DRAFT | CANCELLED | Requester cancels a chat draft (S17). A draft was never matched, so it has no source requests or holds and nothing is released |

A manual match re-run (`POST /shortages/{id}/match`) is allowed only in OPEN or MATCHING (else 409 `invalid_transition`), and returns 409 `conflict` while any source request for the shortage is still open (REQUESTED or TENTATIVE_HOLD): matching re-runs on its own when those requests are declined or expire.

**SourceRequest**
| From | To | Trigger |
|---|---|---|
| REQUESTED | TENTATIVE_HOLD | Source accepts |
| REQUESTED | DECLINED | Source declines (reason optional) |
| REQUESTED | EXPIRED | Response deadline passed |
| REQUESTED, TENTATIVE_HOLD | SUPERSEDED | Another source won (CRITICAL), a split partner declined or expired, or the shortage was cancelled |
| TENTATIVE_HOLD | CONFIRMED | Requester approves |
| TENTATIVE_HOLD | EXPIRED | Hold deadline passed, or recommendation rejected/expired |

**Hold**
| From | To | Trigger |
|---|---|---|
| TENTATIVE | FIRM | Requester approves the recommendation |
| TENTATIVE | RELEASED | Decline, expiry, rejection, superseded request or cancel (§7 step 6) |
| FIRM | RELEASED | Allowed, but no flow releases a FIRM hold yet |
| FIRM | CONSUMED | The driver records PICKED_UP: the batch's `on_hand` is drawn down with it (§9) |

TENTATIVE and FIRM holds are active (§2). CONSUMED is not a release: the stock left with the shipment, so no waiting shortage re-runs for it. Hold rows are SYSTEM rows in the source org (§10).

**Recommendation**: PENDING → APPROVED | REJECTED | ESCALATED | EXPIRED; ESCALATED → APPROVED | REJECTED | EXPIRED. Escalate notifies every APPROVER in the org.

**PurchaseOrder**: SENT → ACKNOWLEDGED → DISPATCHED → DELIVERED; SENT or ACKNOWLEDGED → REJECTED. DISPATCHED → DELIVERED happens when the hospital records the receipt of the order's shipment (§9), as a SYSTEM row in the hospital's org mirrored into the supplier's.

**Shipment**: CREATED → ASSIGNED → PICKED_UP → IN_TRANSIT → DELIVERED → RECONCILED. ASSIGNED → CREATED when unassigned.
- PICKED_UP draws down the source batch and consumes its FIRM hold (§9). A batch that now records less `on_hand` than its hold (a short pickup) does not block it.
- Limitation: one pickup stop per shipment (multi-stop pickups are not supported, including by the S16 route planner). Assigning a shipment whose FIRM holds sit at more than one of the source's facilities returns 409 `conflict`, "Stock is held at more than one of the source's facilities; a shipment can have only one pickup"; the route planner reports such a shipment as infeasible with the same reason instead of planning it.
- Assign and unassign change the carrier, so they take any device (cold box) off the shipment. A device goes on a shipment only while its own org is the carrier (CLAUDE.md rule 6).

## 9. Receipt and reconciliation
- Receipt records expected, received, accepted, rejected, and condition. Invariants: accepted + rejected = received; received ≤ expected.
- If any cold-chain EXCURSION is on record for the shipment, even one that has since RECOVERED (§11: the excursion stays on record), `inspection_note` is required before accepting.
- When every shipment for a shortage has a receipt: total accepted = shortfall → RESOLVED. Total accepted < shortfall → PARTIALLY_RESOLVED, and a residual Shortage is created with `qty_required = shortfall − accepted`, `qty_local_usable = 0`, the same product, priority and shelf-life minimum, `parent_shortage_id` set; it starts matching automatically.
- The residual inherits its parent's excluded sources (`excluded_org_ids`): sources that declined, had a recommendation rejected, or had a purchase order rejected for the parent are not asked again for the residual (§7 step 6).
- Accepted stock is added to the receiver's inventory as a new batch. The source's on_hand and the FIRM hold are reduced at pickup: `on_hand` drops by the hold's qty and the hold becomes CONSUMED.
- Short pickup: if the batch then records less `on_hand` than the hold, pickup still goes ahead. It draws down only what the batch records (never below 0) and consumes the hold; the SYSTEM rows in the source org state the recorded figures (on hand before, held qty, qty drawn down). The hub does not claim what physically left: the shortfall surfaces when the receiver records what arrived, and reconciliation opens the residual as above.

## 10. Audit
- Every state change above writes one AuditLog row in the same database transaction.
- `reason_source = USER` only when the user typed a reason. Otherwise `reason_source = SYSTEM` and the reason is "No reason was entered." (for user actions) or a factual system cause such as "Response deadline passed." (for timers).
- The system never writes statements about physical events that no one recorded.
- Each row belongs to one org. A source request's row goes to the org of the user who acted (the source's accept or decline to the source org, the requester's cancel to the requester's org); a system change of a request (created, expired, superseded after a decline or expiry) to the requester's org. A hold's rows always go to the source org. A hold released because of another org's action is recorded as SYSTEM with a factual cause (e.g. "The requester cancelled the shortage."), never with the other org's user id or typed text.
- A supplier's purchase-order actions (acknowledge, reject, dispatch, and the shipment created on dispatch) follow the same rule: the row is in the supplier's org with its user, mirrored into the hospital's org without the user's id.
- A shipment transition is in the acting user's org, mirrored into the receiving (`to`) org without the user's id. The source's batch and hold changes at pickup are SYSTEM rows in the source org. A device taken off a shipment because assign or unassign changed the carrier is a SYSTEM row in the device's org ("The shipment's carrier changed, so the device was taken off it.").
- A source's accept or decline is also mirrored into the requester's org (so the requester's own trail shows the answer, as in Scenario 1 step 8): the same action, reason and `reason_source`, with `actor_id` null and without `responded_by`.

## 11. Cold chain (S15)
- An excursion = 2 consecutive readings outside the product's [temp_min_c, temp_max_c].
- Device silent for 2 minutes while the shipment is IN_TRANSIT → DEVICE_SILENT event (warning).
- Back in range for 2 consecutive readings → RECOVERED event; the excursion stays on record.

## 12. Reliability and credits (S19)
- score = 40 × acceptance_rate + 25 × on_time_rate + 20 × (1 − discrepancy_rate) + 15 × response_speed, where response_speed = max(0, 1 − median_response_minutes ÷ SLA minutes). Recomputed nightly and after each reconciliation.
- Credits: +1 per 10 units transferred and reconciled, recorded in CreditLedger. They are not spendable in the MVP.

## 13. UI wording by resolution type
- TRANSFER: button "Approve transfer"; afterwards "Stock is now held at the source."
- TRANSFER_SPLIT: "Approve transfers"; afterwards "Stock is now held at each source."
- BUY: "Approve purchase"; afterwards "The order has gone to the supplier."
