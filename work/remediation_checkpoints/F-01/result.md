# F-01 remediation result

## Root cause

An uncommented `DATABASE_URL` in `.env.example` duplicated the active `.env` value. A distributable template therefore became a credential source.

## Affected modules and flow

- `.env.example`: developer/deployment configuration template.
- `.env`: remains the active local secret source; it was not changed or printed.
- `tests/test_infrastructure.py`: prevents an active DB URL from returning to the example.
- Deployment/bootstrap flow: operators copy or consult `.env.example` when preparing an environment.

## Change

- Removed the active `DATABASE_URL` from `.env.example`.
- Retained the existing commented, inert `postgresql+psycopg://...replace-me...` example.
- Added `test_environment_example_never_contains_an_active_database_url`.
- No database schema or migration change.

## Verification

- Focused test: PASS (1/1).
- Exact credential repository scan after change: one match, the active `.env` only.
- `.env.example` active DB URL lines: zero.
- `.env.example` post-change SHA-256: `BBEFBAC66EF24E1BD898CCD8A6D5BAB371C0CADD068E6275498E97DC7ECAE724`.
- Infrastructure regression: 7 passed, 2 failed, 1 skipped. The two failures are the pre-existing test isolation defect caused by omitting `SUPPLY_AI_AUTO_CREATE_SCHEMA=true`; PostgreSQL integration remains skipped because `TEST_POSTGRES_URL` is not configured.

## Status

**LOCAL EXPOSURE FIX VERIFIED. EXTERNAL ROTATION URGENTLY REQUIRED.** Removing a copied credential cannot revoke it. During later PostgreSQL test-harness development, Alembic emitted the configured connection string in a captured traceback before the harness was corrected to suppress it. The value is not repeated here. The database operator must rotate the password outside this repository and update only the securely stored active environment value.
