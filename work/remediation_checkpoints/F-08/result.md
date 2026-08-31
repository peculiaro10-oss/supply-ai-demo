# F-08 result: VERIFIED

Date: 2026-08-30

## Exact fix

- Retail and wholesale checkout prices are now selected from the locked, tenant-owned `Product` row. A client-supplied `unit_price` cannot change either catalog mode.
- Negotiated pricing is explicit: only Admin/Manager may use it; a 5-200 character reason is required; and the price must be between recorded unit cost and the highest current retail/wholesale catalog price.
- `SALE_COMPLETED` now records the server policy and negotiated-line catalog baselines, cost, final price, actor role, and reason in immutable audit metadata.
- The POS sends `price_mode`, exposes negotiation only to Admin/Manager, validates the same bounds for immediate feedback, captures a reason, and carries the policy through the offline outbox. The server remains authoritative.
- Final selling price continues to be stored in `SaleModel.unit_price` and `total_price`; product catalog prices are never mutated.

## Files changed

- `main.py`
- `index.html`
- `tests/test_sale_pricing_policy_postgres.py` (new)
- `work/remediation_checkpoints/F-08/checkpoint.md` (new)
- `work/remediation_checkpoints/F-08/result.md` (new)

No persistence shape changed; no Alembic migration was required.

## Verification

- Python compilation: PASSED.
- Inline JavaScript parse: PASSED (3 scripts).
- F-08 isolated PostgreSQL suite: PASSED, 5/5.
  - server-derived retail and wholesale prices despite malicious submitted prices
  - transaction detail reload and persisted price/total verification
  - authorized Manager negotiation, bounds, required reason, audit metadata, and duplicate replay
  - Staff override rejection and invalid-bound/reason rejection with zero durable writes
  - injected audit failure rolled back header, line, stock, warehouse stock, day, and audit; retry succeeded
  - foreign-tenant product rejection with both tenants unchanged
- Affected end-to-end journey: POS/API-equivalent authenticated checkout -> locked Product pricing -> atomic SaleTransaction/SaleModel/audit write -> transaction-detail reload: PASSED.
- F-02 regression: PASSED, 6/6.
- F-03 regression: PASSED, 2/2.
- F-04 regression: PASSED, 3/3 on isolated rerun.

During the first combined regression run, the PostgreSQL service closed the connection during F-04 test 3 after tests 1-2 had passed. This was an infrastructure exception, not a failed application assertion. Its stranded `cauldra_f04_ffac916e6624` schema was explicitly removed after service recovery, then F-04 passed 3/3 in a fresh schema.

Final cleanup check: `REMAINING_REMEDIATION_SCHEMAS=0`.

## Post-change hashes

- `main.py`: `C8E3CBEF338183DA5E52187422F8917DC2D7D5F43AF59B97948851AC08965E2D`
- `index.html`: `5D3C78714D72DEA20F118F7F4973A64167E15F5B048E3A3A7E0F19AF26610B1B`
- `tests/test_sale_pricing_policy_postgres.py`: `8AD3805DCEA007D085A4BE522FD67BB63D694F97CD56A7D5350D2A69AFF88734`

The externally exposed PostgreSQL credential remains an operator rotation action outside the codebase and was never printed.
