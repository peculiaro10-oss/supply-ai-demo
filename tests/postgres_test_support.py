"""Shared PostgreSQL test-schema helper.

Formalizes the exact pattern already used by tests/test_mutation_idempotency_postgres.py
(and test_registration_atomicity_postgres.py, test_paystack_verification_postgres.py,
test_sales_checkout_atomicity_postgres.py, test_paystack_webhook_atomicity_postgres.py):
create a uniquely-named schema on a real PostgreSQL server, point DATABASE_URL/
PGOPTIONS at it, run `alembic upgrade head` against it in a subprocess, import
`main` bound to that schema, and drop the schema afterward. This module exists
only to remove the copy-pasted setUpClass/tearDownClass boilerplate every one
of those files already repeats — it is not a new testing framework, and it
does not change how any of them behave.

Set TEST_POSTGRES_ADMIN_URL to a PostgreSQL connection string with permission
to create/drop schemas. Every schema created here is uniquely named
(`<prefix>_<12 hex chars>`) and dropped in teardown, even if the calling
test raised — this module never writes to, and never drops, anything outside
a schema it created itself; the application's own `public` schema (where any
real tenant data lives) is never touched.
"""
from __future__ import annotations

import importlib
import os
import re
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
TEST_SECRET = "test-secret-0123456789abcdef0123456789abcdef0123456789abcdef"
ADMIN_URL = os.getenv("TEST_POSTGRES_ADMIN_URL", "").strip()

# Restored verbatim in teardown so one test module's schema override can never
# leak into a different test module/process that happens to run afterward in
# the same interpreter.
_ENV_KEYS_TO_RESTORE = (
    "DATABASE_URL", "PGOPTIONS", "SUPPLY_AI_ENV", "SUPPLY_AI_SECRET_KEY",
    "SUPPLY_AI_DB_SEARCH_PATH",
)


@dataclass
class PostgresTestSchema:
    schema: str
    admin_engine: Any  # sqlalchemy.engine.Engine — typed loosely to avoid importing sqlalchemy at module import time
    original_env: dict
    main: Any  # the freshly-imported `main` module, bound to this schema


def _validate_schema_name(schema: str, prefix: str) -> None:
    if not re.fullmatch(rf"{re.escape(prefix)}_[a-f0-9]{{12}}", schema):
        raise RuntimeError(f"Unsafe generated test schema name: {schema!r}")


def create_postgres_test_schema(prefix: str, extra_env: Optional[dict] = None) -> PostgresTestSchema:
    """Creates a uniquely-named PostgreSQL schema, points DATABASE_URL/
    PGOPTIONS at it, migrates it with `alembic upgrade head` (run as a
    subprocess so it always sees a fresh environment regardless of what this
    process has already imported), then imports `main` bound to that schema.

    Call once per test module/class in setUpModule()/setUpClass(); pair with
    drop_postgres_test_schema() in the matching tearDown. Raises RuntimeError
    if TEST_POSTGRES_ADMIN_URL isn't configured — callers should check
    `ADMIN_URL` first (see the `@unittest.skipUnless(ADMIN_URL, ...)` pattern
    every *_postgres.py test file already uses) so a missing env var produces
    a clean skip, not a failure."""
    if not ADMIN_URL:
        raise RuntimeError("TEST_POSTGRES_ADMIN_URL is not configured")
    from sqlalchemy import create_engine, text

    schema = f"{prefix}_{uuid.uuid4().hex[:12]}"
    _validate_schema_name(schema, prefix)
    admin_engine = create_engine(ADMIN_URL, pool_pre_ping=True)
    with admin_engine.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')

    original_env = {key: os.environ.get(key) for key in _ENV_KEYS_TO_RESTORE}
    os.environ.update({
        "DATABASE_URL": ADMIN_URL,
        "PGOPTIONS": f"-csearch_path={schema}",
        "SUPPLY_AI_DB_SEARCH_PATH": schema,
        "SUPPLY_AI_ENV": "development",
        "SUPPLY_AI_SECRET_KEY": TEST_SECRET,
    })
    if extra_env:
        os.environ.update(extra_env)

    def _restore_env():
        for key, value in original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    migration = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT,
        env=os.environ.copy(), text=True, capture_output=True, check=False,
    )
    if migration.returncode != 0:
        migration_text = f"{migration.stdout}\n{migration.stderr}".lower()
        if "unsupported startup parameter" in migration_text and "options" in migration_text:
            failure_reason = "PostgreSQL pooler rejected the schema-pinning startup option"
        elif "alembic.util.exc.commanderror" in migration_text or "can't locate revision" in migration_text:
            failure_reason = "Alembic revision graph failed"
        else:
            safe_classes = sorted(set(re.findall(
                r"(?:sqlalchemy\.exc|psycopg(?:\.[a-z_]+)*)\.[A-Za-z]+",
                f"{migration.stdout}\n{migration.stderr}",
            )))
            class_hint = f" ({', '.join(safe_classes)})" if safe_classes else ""
            failure_reason = f"PostgreSQL test schema migration failed{class_hint}"
        with admin_engine.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        admin_engine.dispose()
        _restore_env()
        raise RuntimeError(f"{failure_reason}; captured output suppressed to protect credentials")

    try:
        main_module = importlib.import_module("main")
        # Fail closed before any test mutation if the application is not truly
        # bound to the schema we just created. Never fall back to public.
        with main_module.engine.connect() as connection:
            current_schema = connection.execute(text("SELECT current_schema()" )).scalar_one()
            explicit_schemas = connection.execute(text("SELECT current_schemas(false)" )).scalar_one()
        if current_schema != schema or list(explicit_schemas or []) != [schema]:
            raise RuntimeError("PostgreSQL test connection did not bind exclusively to its isolated schema")
    except Exception:
        # Import performs the application's startup connectivity check. If a
        # remote PostgreSQL disconnect happens after migration but before the
        # import completes, the schema is still ours and must not be stranded.
        # Dispose the possibly stale pool and reconnect once solely to remove
        # the exact validated test schema; always restore process environment.
        admin_engine.dispose()
        try:
            cleanup_engine = create_engine(ADMIN_URL, pool_pre_ping=True)
            with cleanup_engine.begin() as connection:
                connection.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            cleanup_engine.dispose()
        finally:
            _restore_env()
        raise
    return PostgresTestSchema(schema=schema, admin_engine=admin_engine, original_env=original_env, main=main_module)


def drop_postgres_test_schema(ctx: PostgresTestSchema, prefix: str) -> None:
    """Tears down exactly what create_postgres_test_schema() set up. Always
    call this from tearDownClass()/tearDownModule() (never conditionally on
    test success) so a failing test still leaves no orphaned schema and no
    leaked environment override — unittest/pytest both run tearDown*
    regardless of whether the tests in between passed or failed."""
    if ctx.main is not None:
        try:
            ctx.main.engine.dispose()
        except Exception:
            pass
    _validate_schema_name(ctx.schema, prefix)
    last_error = None
    for attempt in range(3):
        try:
            ctx.admin_engine.dispose()
            with ctx.admin_engine.begin() as connection:
                connection.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{ctx.schema}" CASCADE')
            last_error = None
            break
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(1)
    ctx.admin_engine.dispose()
    for key, value in ctx.original_env.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    if last_error is not None:
        raise RuntimeError("Could not remove the exact isolated PostgreSQL test schema after three attempts") from last_error
