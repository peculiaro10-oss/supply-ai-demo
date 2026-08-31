# F-03 pre-change checkpoint

- Created: 2026-08-30 Africa/Lagos
- Scope: `main.py` and a new opt-in PostgreSQL webhook atomicity test module.
- Pre-change `main.py` SHA-256: `65DE4FCC7E8927E36AFB40AA892789D4E4263BCC3E5F84E460092800A53382C0`
- Root cause: `PaystackWebhookEvent` was inserted and committed before any business/payment/subscription effect. An exception then made provider retries return `already_processed` forever.
- Existing unique `event_key` is sufficient when marker and DB effects share one transaction; no schema change or Alembic revision is planned.
