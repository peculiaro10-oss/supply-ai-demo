# F-12 result: VERIFIED

Date: 2026-08-31

## Exact fix

- Removed every live `Product.cost_price` fallback from historical sale and Business Day COGS calculations.
- New-sale cost snapshots remain immutable; changing or deleting the current Product cannot change known historical COGS.
- A legacy `SaleModel.unit_cost_at_sale IS NULL` remains explicitly unknown. No historical cost was fabricated or backfilled.
- Mixed known/unknown summaries retain exact revenue, expenses, units, and `known_cogs`, expose unknown counts and `cogs_complete = false`, and return `null` for total COGS/profit/margin that cannot be known honestly.
- Legacy refunds preserve unknown cost as `NULL` in the refund header and lines, and record COGS completeness in the audit event.
- Transaction reload exposes whether line-item cost is known; the Profit and refund UIs render a clear unknown-cost state instead of coercing `null` to zero.

## Files changed

- `main.py`
- `index.html`
- `alembic/versions/0014_unknown_historical_cogs.py` (new)
- `tests/test_historical_cogs_postgres.py` (new)
- `work/remediation_checkpoints/F-12/checkpoint.md` (new)
- `work/remediation_checkpoints/F-12/result.md` (new)

## Migration

`0014_unknown_historical_cogs` follows the independently added `0013_barcode_catalog` migration and makes refund cost snapshots nullable. It performs no historical backfill. The downgrade deliberately refuses because it cannot safely invent values for unknown historical costs.

## Verification

- Python compilation and all three inline JavaScript parse checks: PASSED.
- F-12 isolated PostgreSQL suite: PASSED, 4/4.
  - known new-sale COGS stayed immutable after product cost change and deletion
  - legacy NULL snapshot stayed unknown after cost change/deletion and transaction reload
  - unknown-cost refund persisted NULL header/line costs with honest audit metadata
  - mixed known/unknown summary and tenant isolation were preserved
  - migrated PostgreSQL columns were verified nullable
- End-to-end affected journey: PASSED through checkout -> persistence -> financial summary -> product mutation/deletion -> reload -> refund -> audit -> recalculated summary.
- Post-F-12 F-02 regression: PASSED, 6/6.
- Post-F-12 F-03 regression: PASSED, 2/2.
- Post-F-12 F-04 regression: PASSED, 3/3.
- Final cleanup: `REMAINING_REMEDIATION_SCHEMAS=0`.

## New finding handled during the checkpoint

An independent `0013_barcode_catalog` migration appeared while F-12 was running and temporarily created two Alembic heads. No independent code was overwritten. The F-12 migration was renumbered/rebased to `0014_unknown_historical_cogs`; the final graph has one head.

## Hashes

- `main.py`: `20F8A7EB10BBBC503624C8793F125BD3184A41D6C4DC8AA8750D76E332CE013D`
- `index.html`: `C69664C2493BE14519039320C1E9E6D41A396FDD6C6C75066B3A3E386D0EE8CD`
- `alembic/versions/0014_unknown_historical_cogs.py`: `C0DE60C900F2F5AD63EE599AB49F0BF8A675A5B3D35D0FFA9B8C345CF45E83A3`
- `tests/test_historical_cogs_postgres.py`: `825902254F8EA9330DA4E5B529F5685E4C88B7A84E012622113D1D2484A7B943`

