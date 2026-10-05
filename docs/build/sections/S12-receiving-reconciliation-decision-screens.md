# S12 — Receiving, reconciliation and the hospital decision screens

**Milestone:** M2 · **Depends on:** S08, S09, S11 · **Workstream:** Backend + Frontend · **Can run alongside:** S14

## Read first
- `docs/specs/business-rules.md` — §8 Shortage table, §9 Receipt and reconciliation, §10, §13
- `docs/specs/domain-model.md` — Fulfillment (Receipt, Reconciliation)
- `docs/specs/apps-ai-iot.md` — hospital-web rows marked S12
- `docs/specs/demo-scenarios.md` — Scenario 1 steps 4–8

## Goal
The loop closes: the requester decides on the recommendation in the hospital app, records what actually arrived, and the hub reconciles it, resolving the shortage or opening a residual one for the missing quantity.

## Build
- **Hub models:** Receipt and Reconciliation.
- `POST /shipments/{id}/receipt` enforces the invariants and moves the shipment DELIVERED → RECONCILED.
- **Cold-chain gate:** add `has_open_excursion(shipment)`, returning False until S15 implements it. When it returns True, a missing `inspection_note` gives 400.
- **Reconciliation:** when every shipment for the shortage has a receipt, reconcile per §9. If short, create the residual shortage and start its matching automatically.
- Add accepted stock to the receiver's inventory as a new batch. Write audit rows and emit `reconciliation.completed`.
- **hospital-web:**
  - **Decision panel** on shortage detail: type badge, lines, cost, ETA, explanation, alternatives, and a validity countdown. Buttons and post-approval messages are worded exactly per §13. Reject and Escalate have an optional reason box.
  - **Deliveries list** with live status and ETA.
  - **Receive screen:** expected (read-only), received, accepted, rejected, condition, and an inspection note.
  - **Audit tab** on shortage detail, showing user-entered and system reasons distinctly.

## Out of scope
Cold-chain panels and rules (S15); chat (S17).

## Acceptance criteria
- [ ] Scenario 1 end to end (API test): 790 accepted → PARTIALLY_RESOLVED, a residual shortage of 60 exists with `parent_shortage_id`, and it is matching.
- [ ] Accepting the full quantity → RESOLVED.
- [ ] accepted + rejected ≠ received, or received > expected → 400.
- [ ] Component tests: button text and post-approval message are correct for TRANSFER, TRANSFER_SPLIT and BUY.
- [ ] The audit tab labels USER and SYSTEM reasons differently.
- [ ] Playwright test: Scenario 1 from purchase approval to residual shortage, across hospital-web, supplier-web and delivery-web.

## Verify
```
make test-hub && make test-web && make e2e
```
