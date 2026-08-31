# F-02 pre-change checkpoint

Created: 2026-08-30

Authoritative finding: `work/AUDIT_REPORT_2026-08-30.md` F-02.

## Exact root cause

Checkout validates `Product.quantity` from an unlocked read and later performs
a Python-object decrement. Concurrent checkouts with different transaction IDs
can both validate the same pre-sale quantity and then oversell or lose an
update. `WarehouseStock` has the same unlocked read/write problem, its immediate
lookup omits `business_id`, and `max(0, quantity - requested)` hides rather than
rejects warehouse shortages. Finally, stock and sales commit before their audit
entry, allowing an audit failure to leave a partially completed transaction.

The existing `SaleTransaction` unique constraint correctly protects concurrent
duplicates that share a `client_ref`, but its claim occurs after stock
validation. A duplicate that waits behind the winner can therefore see reduced
stock and fail before it reaches the idempotency claim.

## Affected modules and data flow

`POST /sales/checkout` writes or associates:

- `SaleTransaction`
- `Product.quantity`
- `WarehouseStock.quantity`
- `SaleModel` lines and financial snapshots
- `BusinessDay`
- `BusinessProfile.business_brain_dirty`
- `AuditLog`

All reads and writes must remain tenant-scoped to the authenticated user's
`business_id`.

## Pre-change hashes

- `main.py`: `AD59958F9AA8198A53933C4EC40477086A62031432A4E1A65BC799758C37EB91`
- `alembic/versions/0009_sale_transaction_header.py`: `FC120A14DE4A7AC277D2DA94D59C47012AD8A8D7A44BE700E7787F2036C741A2`

## Minimum-change boundary

- Claim the existing unique checkout header before stock validation.
- Lock all tenant-owned product rows in deterministic ID order.
- Lock the corresponding tenant-owned warehouse rows in deterministic order.
- Revalidate total and source-warehouse stock only after locks are held.
- Remove silent clamping and reject the whole cart on any shortage.
- Commit header, stock, sale lines, business-day association, invalidation, and
  audit exactly once; roll back all of them on any failure.
- No model/schema change is needed, so no new Alembic revision is required.
