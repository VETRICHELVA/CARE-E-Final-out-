# S10 — Supplier app

**Milestone:** M2 · **Depends on:** S03, S09 · **Workstream:** Frontend · **Can run alongside:** S11 (backend)

## Read first
- `docs/specs/apps-ai-iot.md` — Shared rules; supplier-web table
- `docs/specs/api-and-events.md` — S04 rows (supplier offers), S09 rows (purchase orders)
- `docs/specs/business-rules.md` — §8 PurchaseOrder table

## Goal
Suppliers keep prices, lead times and stock current, and handle purchase orders, while seeing network demand for their products without seeing which hospitals are short.

## Build
- **Hub addition:** `GET /network/demand` returns open shortfall totals per product, only for products the caller's supplier org offers, with no hospital identities. Add it to `api-and-events.md`.
- **Dashboard:** new purchase orders, orders to dispatch, and offers not updated in 7 days (these fail the freshness gate).
- **Catalog and offers:** inline edit of price (₹), lead time (hours) and available quantity, with "last updated" shown.
- **Purchase orders:** list and detail; Acknowledge, Reject (optional reason), Mark dispatched. Only valid actions are shown.
- **Network demand:** a table per product.
- Live updates through `useEventStream()`.

## Out of scope
Supplier invoicing and payments.

## Acceptance criteria
- [ ] Scenario 1 step 6, supplier part, works in the UI: Supplier Y acknowledges and dispatches the PO.
- [ ] `/network/demand` never returns hospital names or IDs (API test).
- [ ] An action invalid for the PO's state is hidden, and the hub returns 409 if it is forced.
- [ ] A stale offer (over 7 days) is flagged on the dashboard.

## Verify
```
make test-hub && make test-web && make e2e
```
