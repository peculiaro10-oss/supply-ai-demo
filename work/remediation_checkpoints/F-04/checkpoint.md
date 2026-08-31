# F-04 pre-change checkpoint

Created: 2026-08-30

Authoritative finding: `work/AUDIT_REPORT_2026-08-30.md` F-04.

## Exact root cause

The first-time `charge.success` webhook branch changes the local payment record
to `success` and activates the tenant subscription using only fields from the
signed webhook body. A valid webhook signature authenticates Paystack as the
sender, but the branch does not independently fetch the transaction from
Paystack or reconcile its status, reference, amount, currency, customer, and
tenant/purpose metadata against the server-created `PaymentRecord`.

## Affected modules and data flow

1. `POST /subscription/checkout` creates the expected `PaymentRecord` and sends
   the initialization request to Paystack.
2. `POST /webhooks/paystack` receives `charge.success`.
3. The first-subscription branch writes `PaymentRecord`,
   `BusinessSubscription`, `BusinessProfile`, and `AuditLog` in one transaction.

## Pre-change hashes

- `main.py`: `3DEB96852FEE68E8904F362EE1A77AB2B894078ABF4FD775A7CAB86D80D4097A`
- `tests/test_paystack_webhook_atomicity.py`: `458B9BA241690437529C9CECE5ADBC53CC2A29EB228E4474C077C91F9D746C793`
- `tests/test_paystack_webhook_atomicity_postgres.py`: `6CEDC448A1D10E05E0FB2F54B821B6286871A0381AAC5BA7DBCF1910F86E963F`

## Minimum-change boundary

- Keep signature validation and F-03 transaction semantics unchanged.
- Store the checkout customer identity and purpose in the existing transaction
  metadata; no persistence schema change or Alembic migration is required.
- Before first-time activation, call the existing server-side Paystack verify
  helper and reconcile all server-owned expectations.
- Treat provider unavailability as retryable by rolling back the entire webhook
  transaction; treat an authoritative mismatch as a committed flagged payment
  with no subscription or tenant-plan activation.
