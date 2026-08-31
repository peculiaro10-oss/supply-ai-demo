"""One-off pre-migration backup for the production PostgreSQL database.

pg_dump is not available in this environment, so this performs a logical
backup instead: every row of every table in the `public` schema is
exported to its own JSON file, plus a schema snapshot (columns/indexes) for
comparison after the migration. Read-only — never writes to the database.

Usage (from the project root, with the real .env already in place):
    venv/Scripts/python.exe scripts/backup_postgres_before_migration.py
"""
import sys, os, json, decimal, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
import main
import sqlalchemy as sa

BACKUP_DIR = Path(__file__).resolve().parent.parent / "backups" / f"pg_backup_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"
BACKUP_DIR.mkdir(parents=True, exist_ok=True)


def _json_default(o):
    if isinstance(o, (datetime.datetime, datetime.date)):
        return o.isoformat()
    if isinstance(o, decimal.Decimal):
        return float(o)
    return str(o)


def main_backup():
    with main.engine.connect() as conn:
        tables = [r[0] for r in conn.execute(sa.text(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name"
        )).fetchall()]

        manifest = {"tables": {}, "generated_at": datetime.datetime.utcnow().isoformat()}

        for t in tables:
            rows = conn.execute(sa.text(f'SELECT * FROM "{t}"')).mappings().all()
            data = [dict(r) for r in rows]
            out_path = BACKUP_DIR / f"{t}.json"
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(data, f, default=_json_default, indent=None)
            manifest["tables"][t] = len(data)
            print(f"  {t}: {len(data)} rows -> {out_path.name}")

        # Schema snapshot for post-migration comparison
        cols = conn.execute(sa.text(
            "SELECT table_name, column_name, data_type, is_nullable, column_default "
            "FROM information_schema.columns WHERE table_schema='public' ORDER BY table_name, ordinal_position"
        )).fetchall()
        idx = conn.execute(sa.text(
            "SELECT tablename, indexname, indexdef FROM pg_indexes WHERE schemaname='public' ORDER BY tablename, indexname"
        )).fetchall()

    with open(BACKUP_DIR / "_schema_columns.json", "w", encoding="utf-8") as f:
        json.dump([list(r) for r in cols], f, indent=None)
    with open(BACKUP_DIR / "_schema_indexes.json", "w", encoding="utf-8") as f:
        json.dump([list(r) for r in idx], f, indent=None)
    with open(BACKUP_DIR / "_manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    print(f"\nBackup complete: {BACKUP_DIR}")
    print(f"Tables backed up: {len(tables)}")


if __name__ == "__main__":
    main_backup()
