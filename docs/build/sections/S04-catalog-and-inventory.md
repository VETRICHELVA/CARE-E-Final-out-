# S04 — Catalog and inventory

**Milestone:** M1 · **Depends on:** S02 · **Workstream:** Backend · **Can run alongside:** S03

## Read first
- `docs/specs/domain-model.md` — Identity (ProductAuthorization), Catalog, Inventory
- `docs/specs/business-rules.md` — §2 Transferable quantity
- `docs/specs/api-and-events.md` — S04 rows
- `docs/specs/demo-scenarios.md` — Scenario 1 table (for test numbers)

## Goal
Products, supplier offers and inventory batches exist, and every batch response carries a hub-computed transferable quantity that the client can never set.

## Build
- **Models and migrations:** Product, ProductAuthorization, SupplierOffer, InventoryBatch (including `unit_cost_paise`), VerificationEvent.
- **`app/domain/inventory.py`** (pure functions):
  - `batch_transferable(batch, today, held_qty=0)`, where `held_qty` lets S06 count active holds as reserved.
  - `is_expired(batch, today)`.
  - `days_to_expiry_at(batch, arrival_date)`.
- **Endpoints** from the S04 rows:
  - Batches list, create and update (`transferable` is read-only in schemas).
  - `POST /inventory/batches/{id}/verify`.
  - CSV import with per-row errors (line number + message); each valid row is inserted, invalid rows are skipped.
  - Supplier offers get and put.
  - Products list and get.
- Every batch or offer change writes an audit row.
- **Catalog seed module** (`scripts/seed/catalog.py`): about 40 products, including Surgical Kit A, Rapid Diagnostic Kit (2–8 °C) and IV Cannula 20G. S20 reuses it.

## Out of scope
Matching, holds and screens.

## Acceptance criteria
- [ ] Unit tests: Hospital B → 1,000; Hospital C → 100; an expired batch → 0; never negative; `held_qty` reduces the result.
- [ ] Batch responses include `transferable`; sending it in a request body is ignored or rejected (422).
- [ ] CSV import: a file with 5 valid and 2 invalid rows inserts 5 and reports 2 with line numbers.
- [ ] Editing another org's batch returns 403; a supplier user cannot create inventory batches.
- [ ] Verify updates `last_verified_at` and writes a VerificationEvent.

## Verify
```
make migrate && make test-hub && make lint && make client
```
