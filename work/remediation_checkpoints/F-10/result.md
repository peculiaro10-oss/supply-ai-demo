# F-10 result: VERIFIED (fixed by F-02 dependency)

Date: 2026-08-30

## Exact outcome

The post-remediation production path already contained the systemic F-10 fix from F-02. `sales_checkout` rejects an empty cart before a transaction claim and performs quantity, tenant, inventory, warehouse, and pricing validation before `ensure_open_business_day(..., commit=False)`. Day creation, checkout header, stock decrement, sale lines, and audit commit together only after the full cart succeeds.

No further production-code change was made. A dedicated PostgreSQL regression suite was added to prevent this ordering from regressing.

The shared PostgreSQL test helper also received one narrow runtime-safety change: if application import/startup fails after the helper has created and migrated its isolated schema, it now attempts to remove that exact schema and always restores environment state.

## Files changed

- `tests/test_rejected_checkout_state_postgres.py` (new)
- `tests/postgres_test_support.py` (startup-failure isolated-schema cleanup only)
- `work/remediation_checkpoints/F-10/checkpoint.md` (new)
- `work/remediation_checkpoints/F-10/result.md` (new)

No production persistence shape changed; no migration was required.

## Verification

- F-10 Python compilation: PASSED.
- F-10 isolated PostgreSQL suite: PASSED, 4/4.
  - empty and malformed cart: zero durable writes
  - insufficient stock, invalid negotiated price, and foreign-tenant product: zero durable writes and unchanged stock
  - concurrent rejected requests: zero claims, days, sales, or audits
  - rejected reference -> corrected retry -> duplicate replay: one legitimate day, header, sale line, day audit, sale audit, and one stock decrement
  - persisted transaction detail reload: PASSED
- Post-F-10 F-02 regression: PASSED, 6/6.
- Post-F-10 F-03 regression: PASSED, 2/2 on isolated rerun.
- Post-F-10 F-04 regression: PASSED, 3/3.

The first F-10 attempt ran zero tests because the remote PostgreSQL service disconnected during startup after migration. The exact orphan `cauldra_f10_80309598d334` was removed. During the combined regression, F-03 startup later stalled in the provider connection for over seven minutes and was interrupted; exact orphan `cauldra_f03_5207bac190b0` was removed. F-03 then passed 2/2 in a fresh schema. These were infrastructure failures before assertions, not business-logic failures.

Final cleanup check: `REMAINING_REMEDIATION_SCHEMAS=0`.

## Post-verification hashes

- `main.py` unchanged from F-08: `C8E3CBEF338183DA5E52187422F8917DC2D7D5F43AF59B97948851AC08965E2D`
- `index.html` unchanged from F-08: `5D3C78714D72DEA20F118F7F4973A64167E15F5B048E3A3A7E0F19AF26610B1B`
- `tests/postgres_test_support.py`: `C456A2C212F42BD0C8078938E76D02E219C4F066B2D80E0B71C49DA5B8CBC019`
- `tests/test_rejected_checkout_state_postgres.py`: `1025604F9C846E33C8C90A25664485E40CEE6C4C28DCF917DCBDBDC2B9B4211F`

The PostgreSQL credential remains an operator rotation action and was never printed.
