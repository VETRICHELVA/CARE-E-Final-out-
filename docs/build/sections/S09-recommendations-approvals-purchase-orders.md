# S09 — Recommendations, approvals and purchase orders

**Milestone:** M2 · **Depends on:** S06, S07 · **Workstream:** Backend · **Can run alongside:** S08, S14

## Read first
- `docs/specs/business-rules.md` — §5 (resolution choice), §6, §7 steps 4–6, §8 (Shortage, Recommendation, PurchaseOrder tables), §13
- `docs/specs/domain-model.md` — Matching (Recommendation), Fulfillment (PurchaseOrder, Shipment)
- `docs/specs/api-and-events.md` — S09 rows
- `docs/specs/demo-scenarios.md` — Scenario 1 steps 4–6

## Goal
Once sources hold stock (or straight away for a purchase), the requester gets one clear, explained recommendation and can approve, reject or escalate it. Approval creates shipments or a purchase order, and a supplier's rejection sends the shortage back to matching.

## Build
- **Model:** Recommendation. Also add the minimal **Shipment** model (fields from the domain model; status CREATED only). S11 adds assignment and movement.
- **Creating recommendations:** implement `on_sources_ready` from S06. For BUY plans, create the recommendation immediately after matching. Then shortage → AWAITING_DECISION and emit `recommendation.ready`.
- **Explanation:** built deterministically from template text and candidate data (no AI), e.g. "Hospital B holds 850 units with 180 days of shelf life and can deliver in about 6 h. 3 other sources were not eligible: …". It always lists the BUY alternative.
- **Approve:**
  - Transfer: holds FIRM, requests CONFIRMED, one Shipment per source.
  - Buy: PurchaseOrder SENT.
  - Then shortage → IN_FULFILLMENT. The response carries a `message` using the §13 wording.
- **Reject** (optional reason): release holds, re-run matching.
- **Escalate:** status ESCALATED and a Notification row for every APPROVER in the org.
- **Recommendation expiry:** add to the timer job; release and re-run.
- **Purchase order endpoints:**
  - Acknowledge.
  - Reject: shortage → MATCHING, re-run excluding that supplier.
  - Dispatch: creates the Shipment from the supplier to the hospital.
- Audit and events for every transition.

## Out of scope
Screens (S10, S12); assignment and driving (S11); receipt (S12).

## Acceptance criteria
- [ ] Scenario 1 steps 4–5 (API test): after B declines, the recommendation is BUY from Y with X as an alternative; approval creates a PO, and the message is "The order has gone to the supplier."
- [ ] Approving a TRANSFER creates one shipment; a TRANSFER_SPLIT creates one per source (2–3).
- [ ] Approving an expired or already-decided recommendation returns 409.
- [ ] A PO rejection re-runs matching without that supplier.
- [ ] Every number in the explanation equals the stored candidate data (test).
- [ ] Only a user with `recommendation.approve` in the requesting org can decide (403 otherwise).

## Verify
```
make test-hub && make lint && make client
```
