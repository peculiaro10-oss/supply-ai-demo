# F-08 pre-change checkpoint

Date: 2026-08-30

## Root cause reconfirmed against current code

- `SalesCheckoutItem.unit_price` accepts any finite positive browser value.
- `sales_checkout` copies that value directly into `SaleModel.unit_price` and `total_price` after locking inventory; catalog prices are not consulted.
- The POS exposes transaction-only price editing to Admin, Manager, and Staff and submits the edited value without an override reason.
- `SALE_COMPLETED` records transaction totals but no pricing mode, authorization decision, catalog baseline, or negotiated-price reason.

The F-02 row locks and atomic transaction boundary are sound and are not being redesigned.

## Affected data flow

`index.html` POS mode/editor and offline outbox -> `POST /sales/checkout` -> authenticated tenant user -> checkout validation and F-02 row locks -> `Product` catalog/cost fields -> `SaleTransaction` plus `SaleModel` snapshots in PostgreSQL -> revenue/COGS/profit summaries -> `SALE_COMPLETED` audit metadata -> checkout response, local inventory refresh, and persisted sales reload.

## Minimum policy

- Retail and wholesale modes are derived from the locked tenant-owned Product row; submitted prices cannot alter them.
- A negotiated price is permitted only to Admin/Manager, requires a 5-200 character reason, and must be between current recorded cost and the highest current catalog selling price.
- Staff may continue retail/wholesale checkout but cannot negotiate.
- The final price remains the immutable sale-line price snapshot; override authorization and reason are retained in the existing audit record, so no schema migration is required.

## File hashes before F-08

- `main.py`: `BA5497A9E4BF6372B18BD453845AE28EFDCA4EEE3197A2A8C8F7276A44CFA439`
- `index.html`: `6A6F770E214F96FC1BEDA172A189284F58EB37321D08C566635EBB85625246AC`
- Alembic head at checkpoint: `0011_mutation_idempotency` (no F-08 persistence change planned)

The workspace does not contain Git metadata, so this hash manifest is the recoverable checkpoint mechanism. Independently added notification code is outside this remediation and must be preserved.
