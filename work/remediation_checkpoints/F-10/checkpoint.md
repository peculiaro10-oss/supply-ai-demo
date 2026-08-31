# F-10 pre-verification checkpoint

Date: 2026-08-30

## Current root-cause reconciliation

The audit finding was valid against the pre-F-02 code, where business-day creation happened before empty-cart validation. The current dependency-safe F-02 checkout path has already removed that root cause:

- empty carts fail before a `SaleTransaction` is added;
- quantity, tenant ownership, aggregate stock, warehouse stock, and F-08 pricing policy are validated before any day is created;
- the idempotency header is only flushed, never committed, before validation and is rolled back with a rejected request;
- `ensure_open_business_day(..., commit=False)` is reached only after the entire cart is valid and is committed atomically with stock, sale lines, checkout header, and audit.

No additional production-code change is justified unless PostgreSQL verification disproves those invariants.

## Affected ecosystem

POS validation/offline retry -> authenticated `POST /sales/checkout` -> F-02 claim and tenant-scoped locks -> F-08 price validation -> `BusinessDay`, `SaleTransaction`, `SaleModel`, Product/WarehouseStock, and AuditLog in one PostgreSQL transaction -> transaction detail/business-day reload and POS state refresh.

## Pre-verification hashes

- `main.py`: `C8E3CBEF338183DA5E52187422F8917DC2D7D5F43AF59B97948851AC08965E2D`
- `index.html`: `5D3C78714D72DEA20F118F7F4973A64167E15F5B048E3A3A7E0F19AF26610B1B`

The workspace has no Git metadata, so hashes are the checkpoint mechanism.
