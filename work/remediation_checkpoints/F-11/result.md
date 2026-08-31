# F-11 result: VERIFIED (fixed by F-02 dependency)

Date: 2026-08-30

## Exact outcome

The post-remediation application already contains the systemic fix:

- one persistent `SaleTransaction` header per checkout;
- one shared checkout `client_ref` on every line, including normal online checkouts;
- tenant-scoped uniqueness for write-side identity/idempotency;
- one canonical `checkout_key_expr()` used by financial summary, current Business Day, serialized day, and Sales History;
- separate `transaction_count`, `sale_line_count`, and `units_sold` meanings;
- honest legacy behavior: each line with unknown checkout identity remains one transaction rather than being heuristically grouped.

No F-11 production change or new migration was required. Existing migration `0009_sale_transaction_header.py` is the persistence migration/backfill strategy: additive header creation, no fabricated historical header backfill, and read-side legacy fallback.

## Files changed

- `tests/test_transaction_count_postgres.py` (new)
- `work/remediation_checkpoints/F-11/checkpoint.md` (new)
- `work/remediation_checkpoints/F-11/result.md` (new)

## Verification

- Python compilation: PASSED.
- F-11 isolated PostgreSQL suite: PASSED, 3/3.
  - one two-line/five-unit checkout -> 1 transaction, 2 sale lines, 5 units, correct revenue
  - server-generated transaction identity persisted and transaction detail reloaded
  - two checkouts, three lines, six units -> 2 transactions after duplicate replay
  - close Business Day and Sales History reload preserved 2 transactions / 6 items
  - legacy NULL identity rows remained one transaction per row
  - cross-tenant financial summaries remained isolated
- Post-F-11 F-02 regression: PASSED, 6/6.
- Post-F-11 F-03 regression: PASSED, 2/2.
- Post-F-11 F-04 regression: PASSED, 3/3.
- Final cleanup: `REMAINING_REMEDIATION_SCHEMAS=0`.

## Hashes

- `alembic/versions/0009_sale_transaction_header.py`: `FC120A14DE4A7AC277D2DA94D59C47012AD8A8D7A44BE700E7787F2036C741A2`
- `tests/test_transaction_count_postgres.py`: `D99445AF0946550247E354E43C6027667DD3D6F389BC66C7E362DB3C862500EE`
- `index.html` remained `5D3C78714D72DEA20F118F7F4973A64167E15F5B048E3A3A7E0F19AF26610B1B`.

`main.py` changed independently in the shared workspace during F-11 verification (current hash `65FD6C722A73C717ED9598221905A18DBCC0DFD1185FDCA354B4C81B89F155E7`). F-11 made no edit to it; the verified transaction-count path remained intact. Independent notification work was preserved and is not claimed here.
