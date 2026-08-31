# F-06 remediation result

## Root cause

The active `.env` used bare `postgresql://`, which SQLAlchemy maps to the uninstalled psycopg2 driver. Requirements pin Psycopg 3. The production URL guard simultaneously rejected the documented explicit `postgresql+psycopg://` form.

## Affected modules and flow

- `.env`: active application and Alembic connection source.
- `main.py`: production URL validation and SQLAlchemy engine creation.
- `alembic/env.py`: receives the same active URL and imports application metadata.
- `tests/test_infrastructure.py`: driver-selection regression.
- Startup, health/database connectivity, Alembic release migration and all DB-backed requests.

## Change

- Changed only the active URL's driver prefix to `postgresql+psycopg://`; credentials/host/database were not printed or otherwise changed.
- Allowed the explicit Psycopg form in the production validation guard.
- Added a subprocess regression proving production configuration selects `psycopg` without connecting to the dummy test URL.
- No persistence/schema change; no migration created or applied.

## Verification

- Focused tests: 2/2 passed (Psycopg selection and production SQLite rejection).
- Current application import: dialect `postgresql`, driver `psycopg`.
- Read-only live journey: Alembic `current` returned `0006_performance_indexes (head)`; `SELECT 1` returned 1.
- Infrastructure regression: 8 passed, 2 failed, 1 skipped. The failures remain the pre-existing isolated-test schema setup defect; opt-in PostgreSQL test remains skipped without `TEST_POSTGRES_URL`.
- Post-change hashes: `.env` `D0D26BE89C6C6407E594689B752F18FB44C3D8DCB412B534E04FD8C5D8338B33`; `main.py` `B51E493E124D1FA728D8BE606AD447B99EB8800BB8A3092AD615F787B47BE581`; tests `80B3B1E0D59D07AC3C12B277B8741D7F320C62AE99FD6946D89EB16BFCE631C1`.

## Status

**VERIFIED.** The configured runtime and Alembic now use the installed Psycopg 3 driver against the current live PostgreSQL connection. All verification was read-only.
