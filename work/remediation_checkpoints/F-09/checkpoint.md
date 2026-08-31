# F-09 pre-change checkpoint

Created: 2026-08-30

Authoritative finding: `work/AUDIT_REPORT_2026-08-30.md` F-09, reconciled
against the post-F-02 code.

## Current root cause

- Sales checkout is no longer affected by the original check-then-insert flaw:
  F-02 now claims `SaleTransaction` under a tenant-scoped database unique
  constraint before stock validation.
- Product creation still performs `SELECT Product.client_ref` followed by an
  insert without a unique database claim.
- Expense creation still performs `SELECT Expense.client_ref` followed by an
  insert without a unique database claim.
- Product update has no accepted `client_ref`; the offline outbox therefore can
  replay the same mutation and create duplicate audit/invalidation effects.
- Product create/update and expense frontend paths generate an outbox key only
  after a network exception. If the server committed but its response was lost,
  the queued retry receives a different key and is not idempotent.

## Ecosystem map

- Frontend: `index.html` product create/update and expense submit handlers,
  IndexedDB outbox, `runSync`, targeted local-state patch/reload.
- API/auth: authenticated `POST /products/`, `PATCH /products/{id}` and
  `POST /expenses/`; existing Staff restrictions remain unchanged.
- Business logic/ORM: Product + WarehouseStock + GeneralCatalog + AuditLog +
  Business Brain invalidation; Expense + BusinessDay + AuditLog.
- PostgreSQL: needs an atomic tenant/operation/key uniqueness claim whose
  lifecycle commits or rolls back with the mutation.
- Financial/reload: product pricing/stock must appear once; expenses must affect
  profit/day totals once; duplicate responses must return the original durable
  result so the outbox can safely clear.

## Pre-change hashes

- `main.py`: `084F384B6240B67DAD0A9DEBD496A2B7B3A0939BADA04661E04AD285E7DBE8C3`
- `index.html`: `9A897CEBE8F4D2F159BA8DA1B8172A49F462AF97BF0F46C8AC74B92E67D349C4`
- `alembic/versions/0009_sale_transaction_header.py`: `FC120A14DE4A7AC277D2DA94D59C47012AD8A8D7A44BE700E7787F2036C741A2`

## Minimum-change boundary

- Add one generic mutation claim table with a database unique constraint on
  `(business_id, operation, client_ref)`, request hash, status, and stored
  response.
- Claim inside the same transaction as each affected mutation; a failure rolls
  the claim and all side effects back together.
- Reject reuse of a key with a different request body.
- Generate the stable key before the first frontend network attempt and reuse it
  in the outbox after an ambiguous network failure.
- Do not alter F-02's sale transaction claim.
