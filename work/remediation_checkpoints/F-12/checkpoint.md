# F-12 pre-change checkpoint

Date: 2026-08-31

## Root cause reconfirmed

- New checkouts correctly write `SaleModel.unit_cost_at_sale` once.
- Legacy rows with `unit_cost_at_sale IS NULL` are not immutable in reports: both period and Business Day COGS use `COALESCE(snapshot, Product.cost_price, 0)`.
- Refund creation repeats the same fabrication by copying the live product cost, or zero after product deletion, into a new supposedly historical refund snapshot.
- The Profit UI numerically formats all COGS/profit fields and would coerce a truthful `null` COGS to zero unless explicitly taught the unknown state.

## Affected ecosystem

Checkout snapshot -> `SaleModel` -> PostgreSQL financial aggregates and corrections -> Business Day/period APIs -> Dashboard/Profit UI/export; transaction reload -> refund request -> `RefundTransaction`/`RefundLine` -> refund audit -> later COGS/profit summaries.

## Minimum policy

- Never read current Product cost for historical arithmetic.
- Known snapshots remain summed exactly.
- Any included unknown historical cost makes aggregate `cogs`, `gross_profit`, `net_profit`, and margin unknown (`null`), while exact sales, expenses, units, known-cost subtotal, and explicit unknown counts remain available.
- Refunds of legacy sales remain operationally possible but retain unknown cost as `NULL`; they never manufacture a cost.
- The frontend renders `Unknown` with a clear explanation rather than zero.

## Pre-change hashes

- `main.py`: `5CBC1635F396823A24FE5D6960BC6B800CAEC27BDB288D3ECF534E5C0E77106B`
- `index.html`: `5D3C78714D72DEA20F118F7F4973A64167E15F5B048E3A3A7E0F19AF26610B1B`
- current Alembic head: `0012_legacy_data_backfill` (`9722164411DB2FAC4AC02B26191A7C7035AE781C7071C03704BFCF65BBFAFDF3`)

No reliable historical cost evidence is being assumed or backfilled.
