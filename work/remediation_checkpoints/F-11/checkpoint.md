# F-11 pre-verification checkpoint

Date: 2026-08-30

## Current root-cause reconciliation

The audited bug (`COUNT(SaleModel.id)` treating product lines as checkouts) is no longer present after F-02:

- `SaleTransaction` is the persistent one-row-per-checkout write-side identity with tenant-scoped uniqueness.
- every checkout generates or accepts one `client_ref` and stamps it on every `SaleModel` line;
- `checkout_key_expr()` is the sole read-side checkout identity and current financial summary, current Business Day, serialized day, and Sales History aggregates all use `COUNT(DISTINCT checkout_key_expr())`;
- `sale_line_count` remains separately and honestly named; `units_sold` remains `SUM(quantity)`;
- pre-identity rows with `client_ref IS NULL` fall back to one synthetic key per row because there is no reliable evidence with which to reconstruct multi-line historical checkouts.

Migration `0009_sale_transaction_header.py` added the persistent header without fabricating a historical backfill. No new migration or production change is justified unless verification fails.

## Affected ecosystem

POS one-cart submission -> authenticated tenant checkout -> `SaleTransaction` plus multiple `SaleModel` rows -> PostgreSQL distinct checkout key and line/unit/revenue aggregates -> current Business Day, financial summary, close snapshot, Sales History -> frontend labels `Transactions` and `Items Sold`.

## Pre-verification hashes

- `main.py`: `C8E3CBEF338183DA5E52187422F8917DC2D7D5F43AF59B97948851AC08965E2D`
- `index.html`: `5D3C78714D72DEA20F118F7F4973A64167E15F5B048E3A3A7E0F19AF26610B1B`
- `alembic/versions/0009_sale_transaction_header.py`: existing F-02 dependency migration; unchanged in F-11.

The workspace has no Git metadata; hashes are the checkpoint mechanism.
