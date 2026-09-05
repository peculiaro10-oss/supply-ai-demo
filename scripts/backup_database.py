"""Independent PostgreSQL disaster-recovery backup for Cauldra.

pg_dump (custom/compressed format) -> temporary file -> Cloudflare R2
(S3-compatible). Intended to be run manually for now:

    venv/Scripts/python.exe scripts/backup_database.py     (Windows)
    python scripts/backup_database.py                      (Railway / Linux)

Reuses the SAME `DATABASE_URL` the running application already uses (see
backend/main.py) — no separate/duplicate database URL variable is
introduced. Reads it directly from the environment rather than importing
`main.py`: this script must keep working for disaster recovery even if the
application itself fails to start (missing SUPPLY_AI_SECRET_KEY, a broken
migration, etc.), so it deliberately has no dependency on the app module.

R2 credentials come from the existing Railway variables (already
configured, per the operator): R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY,
R2_ENDPOINT, R2_BACKUP_BUCKET. Nothing is hardcoded; nothing is logged.

Object layout in the bucket: database/<YYYY>/<MM>/cauldra-db-<UTC
timestamp>.dump — see BACKUP_RESTORE.md for the restore procedure. This
script only ever uploads; it never deletes anything (no retention/lifecycle
logic yet — see BACKUP_RESTORE.md for why), and it never touches
production data, only reads it via pg_dump's own consistent-snapshot dump.

Requires the `pg_dump` client binary on PATH (see the Dockerfile's
`postgresql-client` package) and the `boto3` dependency (already required
by backend/requirements.txt for the existing optional S3 upload storage
backend — no new dependency was needed for this script).
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, unquote, urlparse

REQUIRED_ENV_VARS = (
    "DATABASE_URL",
    "R2_ACCESS_KEY_ID",
    "R2_SECRET_ACCESS_KEY",
    "R2_ENDPOINT",
    "R2_BACKUP_BUCKET",
)

# Matches any libpq-style connection string so it can never end up in a log
# line, however it got there (e.g. embedded in a pg_dump error message).
_CONNECTION_STRING_PATTERN = re.compile(r"postgres(?:ql)?\+?[a-z0-9]*://\S*", re.IGNORECASE)


class BackupError(Exception):
    """Raised for any failure that should abort the backup with exit code 1."""


def log(message: str) -> None:
    print(message, flush=True)


def scrub_secrets(text: Optional[str]) -> str:
    """Defense-in-depth: strip any connection string (which embeds a
    password) out of text before it is ever logged, e.g. a pg_dump stderr
    message that happened to echo back its connection target."""
    return _CONNECTION_STRING_PATTERN.sub("[REDACTED CONNECTION STRING]", text or "").strip()


def load_config() -> dict:
    """Reads the required environment variables. Reuses the application's
    own DATABASE_URL rather than introducing a second/duplicate variable.
    Raises BackupError (never logs a value) if anything required is
    missing — this is the "missing R2 configuration" / missing DB config
    failure mode the task asks to handle explicitly."""
    missing = [name for name in REQUIRED_ENV_VARS if not os.getenv(name, "").strip()]
    if missing:
        raise BackupError(f"Missing required environment variable(s): {', '.join(missing)}")
    return {name: os.environ[name].strip() for name in REQUIRED_ENV_VARS}


def parse_database_url(database_url: str) -> dict:
    """Converts the app's DATABASE_URL (which may carry a SQLAlchemy
    `+psycopg`/`+psycopg2` driver suffix — see backend/main.py's own
    _normalize_database_url) into the plain host/port/user/password/dbname/
    sslmode pieces pg_dump's flags expect. Never returns anything that gets
    logged by any caller in this module."""
    normalized = database_url
    if normalized.startswith("postgres://"):
        normalized = "postgresql://" + normalized[len("postgres://"):]
    if normalized.startswith("postgresql+"):
        normalized = "postgresql://" + normalized.split("://", 1)[1]

    parsed = urlparse(normalized)
    dbname = (parsed.path or "").lstrip("/")
    if parsed.scheme != "postgresql" or not parsed.hostname or not dbname:
        raise BackupError("DATABASE_URL is not a usable PostgreSQL connection string.")

    query = parse_qs(parsed.query)
    return {
        "host": parsed.hostname,
        "port": str(parsed.port or 5432),
        "user": unquote(parsed.username or ""),
        "password": unquote(parsed.password or ""),
        "dbname": dbname,
        "sslmode": (query.get("sslmode") or [None])[0],
    }


def backup_filename(now: datetime) -> str:
    """cauldra-db-2026-09-05T10-30-00Z.dump — UTC, colon-free so it is a
    valid filename on every OS."""
    return f"cauldra-db-{now.strftime('%Y-%m-%dT%H-%M-%SZ')}.dump"


def object_key_for(filename: str, now: datetime) -> str:
    """database/<YYYY>/<MM>/<filename> — never the bucket root."""
    return f"database/{now:%Y}/{now:%m}/{filename}"


def new_temp_path() -> Path:
    fd, name = tempfile.mkstemp(prefix="cauldra-db-backup-", suffix=".dump")
    os.close(fd)
    return Path(name)


def run_pg_dump(conn: dict, output_path: Path) -> None:
    """Plain pg_dump custom-format dump — a single consistent MVCC
    snapshot, no exclusive locks, nothing that blocks other writers (this
    is pg_dump's normal default behavior; no special flags are needed to
    get it). The password is passed via the PGPASSWORD environment
    variable, never as a command-line argument, so it never appears in a
    process listing; sslmode is forwarded the same way when the connection
    string specified one (Supabase connection strings normally do)."""
    env = os.environ.copy()
    env["PGPASSWORD"] = conn["password"]
    if conn.get("sslmode"):
        env["PGSSLMODE"] = conn["sslmode"]

    command = [
        "pg_dump",
        "--host", conn["host"],
        "--port", conn["port"],
        "--username", conn["user"],
        "--dbname", conn["dbname"],
        "--format=custom",
        "--no-password",
        "--file", str(output_path),
    ]
    try:
        result = subprocess.run(command, env=env, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise BackupError(
            "pg_dump was not found on PATH. Install the postgresql-client package "
            "(see the Dockerfile)."
        ) from exc

    if result.returncode != 0:
        raise BackupError(f"pg_dump exited with status {result.returncode}: {scrub_secrets(result.stderr)[:2000]}")
    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise BackupError("pg_dump reported success but produced an empty output file.")


def build_r2_client(access_key_id: str, secret_access_key: str, endpoint_url: str):
    """R2 is S3-compatible; boto3 is already a required dependency (used by
    the existing optional S3 upload-storage backend in backend/storage.py)
    so no new dependency was needed for this script."""
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )


def upload_backup(client, bucket: str, local_path: Path, object_key: str) -> None:
    client.upload_file(str(local_path), bucket, object_key)


def verify_upload(client, bucket: str, object_key: str, expected_size: int) -> None:
    """Only after this succeeds may the backup be declared successful — a
    HEAD request confirming the object exists in R2 with the same byte
    size as the local dump that was just uploaded."""
    try:
        head = client.head_object(Bucket=bucket, Key=object_key)
    except Exception as exc:
        raise BackupError(f"Could not verify the uploaded backup in R2 ({exc.__class__.__name__}).") from exc

    remote_size = head.get("ContentLength")
    if remote_size != expected_size:
        raise BackupError(f"Uploaded backup size mismatch (local={expected_size} bytes, remote={remote_size}).")


def main() -> int:
    log("Starting Cauldra database backup...")
    tmp_path: Optional[Path] = None
    success = False
    try:
        config = load_config()
        conn = parse_database_url(config["DATABASE_URL"])

        now = datetime.now(timezone.utc)
        filename = backup_filename(now)
        object_key = object_key_for(filename, now)

        tmp_path = new_temp_path()
        run_pg_dump(conn, tmp_path)
        log("Database dump created successfully.")

        dump_size = tmp_path.stat().st_size
        client = build_r2_client(config["R2_ACCESS_KEY_ID"], config["R2_SECRET_ACCESS_KEY"], config["R2_ENDPOINT"])

        log("Uploading backup to R2...")
        upload_backup(client, config["R2_BACKUP_BUCKET"], tmp_path, object_key)
        verify_upload(client, config["R2_BACKUP_BUCKET"], object_key, dump_size)

        log(f"Backup uploaded successfully: {object_key}")
        success = True
    except BackupError as exc:
        log(f"Backup failed: {exc}")
    except Exception as exc:  # noqa: BLE001 - a backup script must never crash without a clear, safe message
        log(f"Backup failed due to an unexpected error: {exc.__class__.__name__}: {scrub_secrets(str(exc))[:500]}")
    finally:
        if tmp_path is not None and tmp_path.exists():
            try:
                tmp_path.unlink()
                log("Temporary backup removed.")
            except OSError:
                log("Warning: could not remove the temporary backup file.")

    if success:
        log("Backup completed successfully.")
        return 0
    return 1


if __name__ == "__main__":
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass
    sys.exit(main())
