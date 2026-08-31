# F-13 pre-change checkpoint

Date: 2026-08-31

## Exact current root cause

F-13 is the Paystack card-verification refund used by both guest onboarding and authenticated trial start, not the separate POS merchandise-refund subsystem.

- `paystack_refund_best_effort()` catches every provider error and returns no outcome.
- Both callers then set `refunded_at` unconditionally.
- Trial audit text says the verification amount was refunded even when Paystack rejected the request or its outcome was unknown.
- The Create Refund API normally returns an asynchronous `pending` refund; initiation therefore is not success.
- Neither `PaymentRecord` nor `OnboardingAuthorization` stores the provider refund id, provider status, attempt state, or safe reconciliation evidence.
- Retry/replay cannot distinguish “never initiated”, “provider rejected”, “accepted but response lost”, “still pending”, and “processed”.

## Affected ecosystem

New-business plan/payment UI -> `/onboarding/payment/init` -> Paystack verification -> `/onboarding/payment/confirm` -> `OnboardingAuthorization` -> later registration; and existing-business Billing UI -> `/subscription/trial/init` -> Paystack verification -> `/subscription/trial/confirm` -> `PaymentRecord`/`BusinessSubscription` -> audit log. Both flows converge on the Paystack Create/Fetch/List Refund API and signed `refund.*` webhooks, then render callback messaging on reload.

## Minimum systemic policy

- Persist a local refund state of `not_requested`, `pending`, `succeeded`, or `failed`, plus Paystack's raw status and refund id.
- A successful Create Refund response is pending unless Paystack explicitly reports `processed`.
- Only `processed` sets `refunded_at` or emits success audit/customer language.
- Definitive provider rejection records failed and permits an atomic retry; ambiguous network outcomes remain pending and reconcile before any new create call.
- Signed refund webhooks and explicit provider fetch/list responses reconcile state idempotently.
- Trial/card verification remains successful even when its small verification-charge refund is pending or failed; the refund state is reported truthfully and independently.

## Pre-change hashes

- `main.py`: `20F8A7EB10BBBC503624C8793F125BD3184A41D6C4DC8AA8750D76E332CE013D`
- `index.html`: `C69664C2493BE14519039320C1E9E6D41A396FDD6C6C75066B3A3E386D0EE8CD`
- Alembic head: `0014_unknown_historical_cogs`

