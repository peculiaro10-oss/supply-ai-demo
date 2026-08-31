"""One-way, verified SQLite-to-PostgreSQL copy tool.

Run only after `alembic upgrade head` against a new, empty PostgreSQL database.
The source SQLite file is opened read-only and never modified.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from sqlalchemy import MetaData, create_engine, func, select, text


def count_rows(connection, table):
    return connection.execute(select(func.count()).select_from(table)).scalar_one()


def main() -> int:
    parser = argparse.ArgumentParser(description="Copy Cauldra SQLite data into an empty PostgreSQL database.")
    parser.add_argument("--sqlite-path", required=True, help="Path to the source SQLite database; it is never modified.")
    parser.add_argument("--database-url", required=True, help="Target PostgreSQL SQLAlchemy URL.")
    args = parser.parse_args()
    source_path = Path(args.sqlite_path).resolve()
    if not source_path.is_file():
        raise SystemExit(f"SQLite source does not exist: {source_path}")
    if not args.database_url.startswith(("postgresql://", "postgresql+psycopg://")):
        raise SystemExit("Target must be a PostgreSQL URL.")

    source = create_engine(f"sqlite:///{source_path.as_posix()}")
    target = create_engine(args.database_url)
    source_meta, target_meta = MetaData(), MetaData()
    source_meta.reflect(bind=source)
    target_meta.reflect(bind=target)
    tables = [name for name in target_meta.sorted_tables if name.name in source_meta.tables]
    with source.connect() as source_conn, target.begin() as target_conn:
        nonempty = [(table.name, count_rows(target_conn, table)) for table in tables if count_rows(target_conn, table)]
        if nonempty:
            raise SystemExit(f"Target must be empty; found rows in: {nonempty}")
        expected = {table.name: count_rows(source_conn, source_meta.tables[table.name]) for table in tables}
        for target_table in tables:
            source_table = source_meta.tables[target_table.name]
            # Older SQLite files can predate additive columns. Copy the shared
            # columns only so PostgreSQL defaults/nullability handle fields that
            # did not exist yet; never invent or rewrite source values.
            shared = [name for name in target_table.c.keys() if name in source_table.c.keys()]
            rows = [{name: row[name] for name in shared} for row in source_conn.execute(select(source_table)).mappings()]
            if rows:
                target_conn.execute(target_table.insert(), rows)
            actual = count_rows(target_conn, target_table)
            if actual != expected[target_table.name]:
                raise RuntimeError(f"Row-count mismatch for {target_table.name}: expected {expected[target_table.name]}, got {actual}")
        # Preserve explicit integer IDs for future inserts after the copy.
        for table in tables:
            if "id" in table.c:
                target_conn.execute(text("SELECT setval(pg_get_serial_sequence(:table_name, 'id'), COALESCE((SELECT MAX(id) FROM " + table.name + "), 1), true)"), {"table_name": table.name})
    print("Migration verified. Source row counts:", expected)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
