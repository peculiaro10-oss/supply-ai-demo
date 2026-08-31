# F-09 remediation result

Status: VERIFIED on PostgreSQL

## Exact fix

- Preserved F-02's existing database-enforced `SaleTransaction` checkout claim.
- Added `MutationIdempotency`, uniquely constrained by
  `(business_id, operation, client_ref)`, with a request hash, processing state,
  committed response, and completion timestamp.
- Product creation, product update, and expense creation now claim their key in
  the same transaction as all business writes and audits.
- Reuse of a key with different request data returns 409 rather than replaying
  an unrelated result.
- Failure rolls back both claim and mutation, allowing a clean retry.
- Expense auto-opened Business Day creation now participates in the expense
  transaction instead of committing the claim prematurely.
- Frontend product create/update and expense flows generate a stable key before
  the first network attempt and reuse it in the outbox after an ambiguous
  network failure.

## Persistence

- Added Alembic revision `0011_mutation_idempotency`, based on the independently
  present `0010_notifications` head.
- Fresh SQLite and isolated PostgreSQL migrations to head passed.
- The migration safely handles the repository's current dynamic baseline
  behavior by checking whether the new table already exists.

## Verification

- F-09 PostgreSQL suite: 6/6 passed.
  - concurrent duplicate product creation
  - concurrent duplicate product update
  - concurrent duplicate expense creation
  - rollback -> retry -> durable replay
  - changed-payload key conflict and tenant-scoped key reuse
  - validation failure with zero durable writes
- Reload/persistence verified through authenticated GET `/products/` and
  GET `/expenses/` after mutations.
- Frontend inline JavaScript syntax: 5/5 script blocks passed.
- Fresh Alembic SQLite migration smoke: passed.
- Targeted Business Day/expense/sale idempotency regressions: 3/3 passed.
- PostgreSQL regression gates:
  - F-02: 6/6 passed
  - F-03: 2/2 passed
  - F-04: 3/3 passed
- Temporary remediation schemas remaining: 0.

## Files changed for F-09

- `main.py`
- `index.html`
- `alembic/versions/0011_mutation_idempotency.py`
- `tests/test_mutation_idempotency_postgres.py`
- `work/check_remediation_schemas.py`
- `work/drop_remediation_schema.py`
- `work/remediation_checkpoints/F-09/checkpoint.md`
- `work/remediation_checkpoints/F-09/result.md`

## Post-change hashes

- `main.py`: `18EA08ADAEE61470DDDA07B877ECF3939ACB637E51CFD46DFA6B2735A6F39EDA`
- `index.html`: `0CED209DE6BFEBD438EB557F5E18023321146C923FCC16301298AFA891734273`
- `alembic/versions/0011_mutation_idempotency.py`: `197545FC52A8E11F7717D335DF13C1457BF7EA6197011F646E2A78E34C024567`
- `tests/test_mutation_idempotency_postgres.py`: `AF15029700AB6A0491CC4AA8EF7E3BE28D08B10A8BA839C1ADAD03990F5369EB`

## Newly discovered runtime concern

An independently introduced notification change briefly left the application
unimportable while its schema and routes were being written and added a pinned
`pywebpush` dependency not present in the pre-existing disposable test virtual
environment. The repository reached a stable import state without an F-09 code
change; the disposable environment was refreshed from `requirements.txt`.
