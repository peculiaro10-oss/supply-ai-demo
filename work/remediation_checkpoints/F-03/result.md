# F-03 remediation result

## Root cause

The webhook committed its unique event marker before applying payment/subscription effects. Any later exception left a durable marker, causing every provider retry to return `already_processed` without repairing state.

## Affected modules and flow

- `main.py`: `PaystackWebhookEvent`, `/webhooks/paystack`, `get_or_create_subscription`.
- Payment records, subscription activation/upgrades/renewals/failures/cancellations, business plan mirror and audit logs.
- SQLite and PostgreSQL rollback/retry/concurrent-duplicate regression modules.

## Change

- The unique marker is flushed, not committed, before processing.
- Every database commit inside the webhook became a flush.
- Marker and database effects commit once at successful completion.
- An unhandled failure rolls marker and effects back together.
- Concurrent duplicates resolve through the unique event key; one delivery applies the effect and the other returns `already_processed`.
- `get_or_create_subscription(..., commit=False)` prevents its legacy helper commit from splitting the webhook transaction.
- No persistence shape changed; no Alembic migration was required.

## Verification

- Python compilation: PASS.
- Isolated rollback -> retry -> duplicate journey: 1/1 PASS.
- PostgreSQL injected-failure/retry test: PASS; failed attempt left no marker, payment change, subscription change or audit, and retry applied exactly once.
- PostgreSQL simultaneous duplicate delivery: PASS; one `received`, one `already_processed`, one marker and one activation audit.
- Infrastructure regression: 10 passed, 0 failed, 1 skipped.
- Authorized test schema was migrated through head and removed; final remediation-schema count: zero.

## Status

**VERIFIED FOR POSTGRESQL ROLLBACK, RETRY AND CONCURRENT DUPLICATE DELIVERY.**

Post-change hashes: `main.py` `3DEB96852FEE68E8904F362EE1A77AB2B894078ABF4FD775A7CAB86D80D4097A`; SQLite regression `458B9BA241690437529C9CECE5ADBC53CC2A1FEDB134B9C9E5E62180E53F8650`; PostgreSQL regression `6CEDC448A1D10E05E0FB2F54B821B628687193D85C6B0BA90DB8DF6F1D23E41B`.
