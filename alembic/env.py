import os
import re
from logging.config import fileConfig
from alembic import context

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)

# Alembic only imports the model metadata; schema creation and legacy data
# backfills must never occur as a side effect of a migration command.
os.environ.setdefault("SUPPLY_AI_AUTO_CREATE_SCHEMA", "false")
os.environ.setdefault("SUPPLY_AI_SECRET_KEY", "alembic-metadata-only-secret-not-for-production-0123456789")
# main.py's own startup connectivity check (verify_database_connectivity())
# is for the running application process, not for Alembic, which manages
# its own connection lifecycle below (run_migrations_online/offline) — a
# redundant connection attempt here would also break `alembic upgrade head
# --sql` (offline mode), which deliberately never connects to a real database.
os.environ.setdefault("SUPPLY_AI_SKIP_DB_STARTUP_CHECK", "true")
from main import Base

target_metadata = Base.metadata
database_url = os.getenv("DATABASE_URL", "").strip()
if not database_url:
    raise RuntimeError("DATABASE_URL is required for Alembic migrations.")
config.set_main_option("sqlalchemy.url", database_url)

def run_migrations_offline() -> None:
    context.configure(url=database_url, target_metadata=target_metadata, literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()

def _ensure_wide_alembic_version_column(connection) -> None:
    """Alembic's own alembic_version.version_num column defaults to
    VARCHAR(32) (see alembic.ddl.impl.DefaultImpl.version_table_impl) — too
    narrow for this project's descriptive revision ids: several already
    exceed 32 characters (e.g. "0018_platform_owner_control_panel" at 33,
    "0019_general_catalog_identity_hardening" at 40), which makes Alembic's
    own internal `UPDATE alembic_version SET version_num=...` fail with
    "value too long for type character varying(32)" partway through ANY
    fresh `alembic upgrade head` run — discovered while verifying migration
    0019 against a disposable test database. Fixed here, once, for every
    invocation: idempotent and safe whether the table is missing, already
    narrower, or already wide enough. Touches only Alembic's own bookkeeping
    table — never an application table, never Base.metadata."""
    connection.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS alembic_version ("
        "version_num VARCHAR(255) NOT NULL, "
        "CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num))"
    )
    connection.exec_driver_sql("ALTER TABLE alembic_version ALTER COLUMN version_num TYPE VARCHAR(255)")

def run_migrations_online() -> None:
    connectable = config.attributes.get("connection")
    if connectable is None:
        from sqlalchemy import engine_from_config, pool
        connectable = engine_from_config(config.get_section(config.config_ini_section, {}), prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        test_schema = os.getenv("SUPPLY_AI_DB_SEARCH_PATH", "").strip()
        if test_schema:
            if os.getenv("SUPPLY_AI_ENV", "development").strip().lower() == "production" or not re.fullmatch(r"cauldra_[a-z0-9_]+_[a-f0-9]{12}", test_schema):
                raise RuntimeError("SUPPLY_AI_DB_SEARCH_PATH is restricted to generated non-production test schemas.")
            # Own the outer transaction. Without this, SET starts an implicit
            # transaction that Alembic sees as externally managed and the
            # connection context rolls all DDL back on exit. SET LOCAL also
            # guarantees every migration statement stays on the same pooler
            # backend and cannot fall through to public.
            with connection.begin():
                connection.exec_driver_sql(f'SET LOCAL search_path TO "{test_schema}"')
                current_schema = connection.exec_driver_sql("SELECT current_schema()").scalar_one()
                explicit_schemas = connection.exec_driver_sql("SELECT current_schemas(false)").scalar_one()
                if current_schema != test_schema or list(explicit_schemas or []) != [test_schema]:
                    raise RuntimeError("Alembic did not bind exclusively to the isolated test schema.")
                _ensure_wide_alembic_version_column(connection)
                context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
                with context.begin_transaction():
                    context.run_migrations()
        else:
            with connection.begin():
                _ensure_wide_alembic_version_column(connection)
            context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
            with context.begin_transaction():
                context.run_migrations()

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
