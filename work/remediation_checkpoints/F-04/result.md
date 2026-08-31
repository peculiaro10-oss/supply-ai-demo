# F-04 remediation result

Status: VERIFIED on PostgreSQL

## Change

- `/subscription/checkout` now stores the server-selected customer email and
  `subscription` purpose alongside business, plan, and interval in the existing
  `PaymentRecord.transaction_metadata` field and sends that same metadata to
  Paystack.
- The first-time subscription webhook calls the existing server-side Paystack
  Verify Transaction API before activation.
- Verification reconciles transaction status, exact reference, amount,
  currency, provider transaction ID presence, customer email, business ID,
  plan, billing interval, and purpose.
- Provider unavailability explicitly rolls back the webhook marker and all
  effects for safe retry.
- An authoritative mismatch commits a
  `flagged_verification_mismatch` payment and tenant-scoped audit entry, with no
  subscription or business-plan activation.
- A successful verification persists the Paystack transaction ID and
  verification source in existing metadata before atomically activating.

No schema change was required; therefore no Alembic migration was created.

## Verification

- PostgreSQL F-04 suite: 3/3 passed.
  - complete checkout -> webhook -> subscription summary journey
  - authoritative multi-field/tenant mismatch with no partial or cross-tenant writes
  - provider failure rollback -> clean retry -> duplicate idempotency
- PostgreSQL F-03 concurrency/atomicity regression: 2/2 passed.
- SQLite webhook rollback regression plus infrastructure regression: 11 passed,
  1 PostgreSQL-environment test skipped because that separate variable was not
  configured; the explicit PostgreSQL suites above ran against the authorized
  database connection.
- Temporary schema cleanup: `REMAINING_REMEDIATION_SCHEMAS=0`.
- External Paystack calls were mocked; no real charge or customer record was used.

## Post-change hashes

- `main.py`: `AD59958F9AA8198A53933C4EC40477086A62031432A4E1A65BC799758C37EB91`
- `tests/test_paystack_verification_postgres.py`: `AA4DB9DAC16CAF570DE1B8D746343FBF993E0D5C77BA0E78FA24018ABA1ED6AB`
- `tests/test_paystack_webhook_atomicity.py`: `106411D197A055A3BEA379A70427CEFAEB4654EDBF595CF37BC4CCB52EB252B2`
- `tests/test_paystack_webhook_atomicity_postgres.py`: `31ADFB2358606C8C08E8C6495D677D59EA4326DCD51606A24AF29B0EB10145AA`
