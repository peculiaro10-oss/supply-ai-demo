# F-02 remediation result

Status: VERIFIED on PostgreSQL

## Change

- Checkout claims the existing unique `SaleTransaction` header before stock
  validation, preserving concurrent replay semantics even when the winning
  request consumes the last unit.
- All requested tenant-owned `Product` rows are selected `FOR UPDATE` in
  deterministic product-ID order.
- All corresponding tenant-owned `WarehouseStock` rows are explicitly scoped
  by `business_id` and selected `FOR UPDATE` in deterministic order.
- Combined per-product quantities are revalidated after locks are held against
  both total product stock and the selected source warehouse.
- Silent warehouse-stock clamping was removed; shortages reject and roll back
  the complete cart.
- Checkout can auto-open its Business Day without an intermediate commit.
  Missing-day creation is serialized on the tenant row and is committed with
  the checkout.
- Header, product stock, warehouse stock, sale lines/snapshots, business-day
  association, Business Brain invalidation, and both relevant audit records now
  commit exactly once. Any exception explicitly rolls the transaction back.

No new persistence shape was required. Existing additive migration
`0009_sale_transaction_header.py` supplies the database-enforced idempotency
header and was exercised by every fresh PostgreSQL test-schema migration.

## Verification

- PostgreSQL F-02 suite: 6/6 passed.
  - complete authenticated checkout -> transaction-detail journey
  - two different concurrent references competing for one unit: exactly one
    200 and one 409; one unit/sale/header/audit only
  - two concurrent requests with the same reference after the last unit sells:
    two 200 responses, exactly one marked duplicate, one stock decrement/write
  - multi-line shortage rollback: no header, day, stock, sale, or audit write
  - injected post-stock audit failure rollback: no partial writes
  - foreign-tenant product rejection: neither tenant changed
- Existing business-day/sales regression suite: 32/32 passed.
- Python compilation check passed.
- Temporary schema cleanup: `REMAINING_REMEDIATION_SCHEMAS=0`.
- Tests used only synthetic tenant, user, product, stock, sale, and payment data.

## Post-change hashes

- `main.py`: `0CE12FC7A562E1EC49EA77F9315B0C4EBCDE1A628EA41812F34A7A9E44259F34`
- `tests/test_sales_checkout_atomicity_postgres.py`: `19C437E27159AFE424B3B02805B9FD04ECD8573B3A87B37A120D5630C7502E8F`
- `alembic/versions/0009_sale_transaction_header.py`: `FC120A14DE4A7AC277D2DA94D59C47012AD8A8D7A44BE700E7787F2036C741A2`
