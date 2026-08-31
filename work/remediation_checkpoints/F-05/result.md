# F-05 remediation result

## Root cause

The authorization CAS, business, admin and subscription each committed separately. Once an earlier commit succeeded, a later failure could not return the authorization or remove the partial tenant.

## Affected modules and flow

- `main.py`: `/auth/register-business`, onboarding authorization, business/admin/default warehouse/trial/audit/refresh-session persistence, post-registration Paystack scheduling.
- `tests/test_infrastructure.py`: existing admin/manager/staff lifecycle setup required explicit isolated schema creation.
- `tests/test_registration_atomicity_postgres.py`: opt-in isolated PostgreSQL rollback/success/race coverage.
- Frontend journey: selected/verified plan -> registration -> auto-login -> `/auth/me` -> logout -> fresh-client business-scoped admin login.

## Change

- One transaction now owns authorization consumption, tenant, admin, warehouse, trial, core audits and initial refresh session.
- ORM `flush()` obtains IDs without durable intermediate commits.
- Any pre-commit HTTP/database failure explicitly rolls back the whole ecosystem.
- Paystack recurring scheduling remains after the durable registration commit. Provider/follow-up-audit failure cannot convert a successful registration into a misleading retry/409 response.
- Fixed the two affected isolated infrastructure tests to explicitly enable their temporary SQLite schema; this P2 test-only change was required to verify the P0 registration work.
- No schema change; no Alembic migration created or applied.

## PostgreSQL verification

- Unique temporary schema, migrated through Alembic head, synthetic data only.
- Injected `BusinessSubscription` insert failure: PASS; authorization remained verified/unconsumed and no business/user survived.
- Successful full ecosystem + browser-equivalent auth journey: PASS.
- Two simultaneous requests using one authorization: PASS; statuses 200/409, exactly one business, authorization consumed once.
- 3/3 PostgreSQL tests passed; temporary schema count returned to zero.

## Regression

- Focused existing admin registration/logout/restart journey: 1/1 passed.
- Infrastructure suite: 10 passed, 0 failed, 1 skipped (`TEST_POSTGRES_URL` legacy opt-in only).
- Refresh rotation/session suite: 10/10 passed.
- Post-change hashes: `main.py` `65DE4FCC7E8927E36AFB40AA892789D4E4263BCC3E5F84E460092800A53382C0`; infrastructure tests `C3AA148B3568B561AF3C0499A31545B1146D4D05397154C8807FF50F11EFE133`; PostgreSQL tests `6E9641C5B91CD2B1F961FACF94F7D68B0CF8237376D237AD3AA6885891BA0DBB`.

## Status

**VERIFIED FOR THE TESTED POSTGRESQL AND AUTH JOURNEYS.** No real customer data or public-schema row was written.
