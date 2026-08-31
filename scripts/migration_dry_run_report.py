"""Read-only dry-run report for the SQLite -> PostgreSQL migration.

Performs ZERO writes to either database. It reflects both schemas exactly the
way scripts/migrate_sqlite_to_postgres.py does (same reflection calls, same
`target_meta.sorted_tables` dependency order, same "shared columns only"
column-selection rule), so the plan printed here is a faithful preview of
what that script would actually do -- it does not insert, update, delete,
alter, or drop anything anywhere.

Usage:
    python scripts/migration_dry_run_report.py --sqlite-path supply_ai.db --database-url "$DATABASE_URL"
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
from sqlalchemy import MetaData, create_engine, func, select, inspect

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


SQLITE_TO_PG_TYPE_NOTES = {
    "DATETIME": "TIMESTAMP (routine dialect mapping, not a mismatch)",
    "BOOLEAN": "BOOLEAN (SQLite stores as 0/1 integer; SQLAlchemy Core handles the conversion on typed insert)",
}


def count_rows(connection, table) -> int:
    return connection.execute(select(func.count()).select_from(table)).scalar_one()


def describe_type(col) -> str:
    return type(col.type).__name__


def main() -> int:
    parser = argparse.ArgumentParser(description="Dry-run report only -- makes no changes to either database.")
    parser.add_argument("--sqlite-path", default=None, help="Defaults to SUPPLY_AI_DATABASE_PATH/.env or ./supply_ai.db")
    parser.add_argument("--database-url", default=None, help="Defaults to DATABASE_URL from .env (never printed)")
    args = parser.parse_args()

    database_url = args.database_url or os.getenv("DATABASE_URL", "").strip()
    sqlite_path_arg = args.sqlite_path or os.getenv("SUPPLY_AI_DATABASE_PATH", "supply_ai.db")

    source_path = Path(sqlite_path_arg).resolve()
    if not source_path.is_file():
        raise SystemExit(f"SQLite source does not exist: {source_path}")
    if not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
        raise SystemExit("Target must be a PostgreSQL URL (DATABASE_URL not set or not Postgres).")

    source = create_engine(f"sqlite:///{source_path.as_posix()}")
    target = create_engine(database_url)

    source_meta, target_meta = MetaData(), MetaData()
    source_meta.reflect(bind=source)
    target_meta.reflect(bind=target)

    source_insp = inspect(source)
    target_insp = inspect(target)

    # Exactly the same table selection + ordering the real script will use.
    ordered_tables = [t for t in target_meta.sorted_tables if t.name in source_meta.tables]
    dest_only_tables = [t.name for t in target_meta.sorted_tables if t.name not in source_meta.tables]
    source_only_tables = [n for n in source_meta.tables if n not in target_meta.tables]

    print("=" * 100)
    print("DRY-RUN MIGRATION REPORT -- NO CHANGES MADE TO EITHER DATABASE")
    print("=" * 100)

    print(f"\nSource SQLite file: {source_path}")
    print(f"Source tables reflected: {len(source_meta.tables)}")
    print(f"Destination tables reflected: {len(target_meta.tables)}")
    print(f"Tables that will be migrated (present in both, dependency order): {len(ordered_tables)}")
    if source_only_tables:
        print(f"\n[INFO] Tables in SQLite with no destination counterpart (will be SKIPPED, not migrated): {source_only_tables}")
    if dest_only_tables:
        print(f"[INFO] Tables in Supabase with no source counterpart (untouched, nothing to migrate into them): {dest_only_tables}")

    blocking_issues = []
    with source.connect() as sconn, target.connect() as tconn:
        dest_nonempty = [(t.name, count_rows(tconn, t)) for t in ordered_tables if count_rows(tconn, t)]
        if dest_nonempty:
            blocking_issues.append(f"Destination is NOT empty for: {dest_nonempty} -- the real script refuses to run against a non-empty target.")

        print("\n" + "-" * 100)
        print(f"{'#':<4}{'SOURCE TABLE':<30}{'DEST TABLE':<30}{'SRC ROWS':<10}{'EXPECTED DEST':<14}")
        print("-" * 100)
        rows_summary = []
        for i, ttable in enumerate(ordered_tables, start=1):
            stable = source_meta.tables[ttable.name]
            src_count = count_rows(sconn, stable)
            rows_summary.append((i, ttable.name, src_count))
            print(f"{i:<4}{ttable.name:<30}{ttable.name:<30}{src_count:<10}{src_count:<14}")

        print("\n" + "=" * 100)
        print("PER-TABLE COLUMN TRANSFER PLAN")
        print("=" * 100)
        for i, ttable in enumerate(ordered_tables, start=1):
            stable = source_meta.tables[ttable.name]
            src_cols = set(stable.c.keys())
            dest_cols = set(ttable.c.keys())
            shared = sorted(src_cols & dest_cols)
            src_only = sorted(src_cols - dest_cols)   # would be silently dropped by the real script
            dest_only = sorted(dest_cols - src_cols)  # will receive column default / NULL, not sourced from SQLite
            print(f"\n[{i}] {ttable.name}")
            print(f"    columns transferred ({len(shared)}): {shared}")
            if src_only:
                print(f"    [FLAG] columns in SQLite but NOT in destination -- WILL BE DROPPED, not migrated: {src_only}")
                blocking_issues.append(f"{ttable.name}: source-only columns would be silently dropped: {src_only}")
            if dest_only:
                nullable_info = []
                for cname in dest_only:
                    col = ttable.c[cname]
                    nullable_info.append(f"{cname} (nullable={col.nullable}, default={col.default or col.server_default})")
                print(f"    columns intentionally omitted (exist only in destination, will use column default/NULL): {nullable_info}")
                # A destination-only column that is NOT NULL and has no default would break the insert.
                for cname in dest_only:
                    col = ttable.c[cname]
                    if not col.nullable and col.default is None and col.server_default is None:
                        blocking_issues.append(f"{ttable.name}.{cname}: destination-only column is NOT NULL with no default -- insert would fail")
            # Type comparison (informational; routine dialect mapping vs real mismatch)
            for cname in shared:
                s_type = describe_type(stable.c[cname])
                d_type = describe_type(ttable.c[cname])
                if s_type != d_type:
                    print(f"    type note: {cname}: SQLite reflects as {s_type} -> Postgres column is {d_type} (handled by typed Core insert)")

        print("\n" + "=" * 100)
        print("PRIMARY KEY / SEQUENCE SYNC PLAN")
        print("=" * 100)
        for ttable in ordered_tables:
            if "id" in ttable.c:
                print(f"  {ttable.name}: existing 'id' values preserved as-is (copied verbatim); "
                      f"sequence will be reset via setval(pg_get_serial_sequence('{ttable.name}','id'), MAX(id)) after copy")

    print("\n" + "=" * 100)
    print("SCHEMA MISMATCHES THAT WOULD BLOCK A CLEAN MIGRATION")
    print("=" * 100)
    if blocking_issues:
        for issue in blocking_issues:
            print(f"  [BLOCKING] {issue}")
    else:
        print("  None found.")

    print("\n" + "=" * 100)
    print(f"OVERALL: {'NOT READY -- resolve blocking issues above' if blocking_issues else 'READY'}")
    print("=" * 100)
    return 1 if blocking_issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
