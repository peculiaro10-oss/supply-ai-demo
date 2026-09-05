"""Independent Supabase Storage disaster-recovery backup for Cauldra.

Copies every object out of the private Supabase Storage bucket
(``cauldra-private`` by default) into the SAME Cloudflare R2 bucket the
database backups already use (see scripts/backup_database.py), under
``storage/<supabase bucket>/<original object path>`` — the exact original
path and filename, preserved exactly. Backup-only: this script never
deletes, overwrites, or modifies anything in Supabase, and never deletes
anything already in R2. It only ever adds objects to R2.

Usage:
    venv/Scripts/python.exe scripts/backup_storage.py     (Windows)
    python scripts/backup_storage.py                      (Railway / Linux)

Reuses the application's EXISTING server-side Supabase credential
(backend/supabase_client.py — SUPABASE_URL + SUPABASE_SECRET_KEY, or the
legacy SUPABASE_SERVICE_ROLE_KEY) rather than introducing a new Supabase
environment variable: that module already validates the configured key is
genuinely service-role-capable (rejects an anon/publishable key), which is
exactly what is required to list/download every object in a PRIVATE bucket
regardless of Row Level Security. This script does NOT import
backend/main.py — no FastAPI app, no database engine, no app-startup
dependency — so it keeps working for disaster recovery even if the
application itself cannot start.

R2 credentials reuse the exact same variables scripts/backup_database.py
already uses: R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_ENDPOINT,
R2_BACKUP_BUCKET (the same R2 bucket — this just writes under a different
key prefix, "storage/..." instead of "database/...").

Nothing is hardcoded and nothing is logged: every secret value actually
loaded from the environment is registered with scrub_secrets() (see
_register_secret()) so it can never leak into a log line or an exception
message, and no secret is ever passed as a command-line argument anywhere
in this script (there is no subprocess call at all here, unlike
backup_database.py's pg_dump).
"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

REQUIRED_R2_ENV_VARS: Tuple[str, ...] = ("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_ENDPOINT", "R2_BACKUP_BUCKET")

# Reuses the app's existing SUPABASE_STORAGE_BUCKET variable when it is set;
# defaults to the fixed target this script exists to back up otherwise.
DEFAULT_SUPABASE_BUCKET = "cauldra-private"

# Supabase Storage's list() API returns at most this many entries per call
# (per folder level) — iter_all_objects() paginates within each folder.
LIST_PAGE_SIZE = 100

# Fallback, pattern-based redaction for secret shapes we didn't get a
# chance to explicitly register (see _register_secret / scrub_secrets):
# Supabase JWTs, sb_secret_/sb_publishable_ keys, and any URL with
# embedded credentials.
_SECRET_PATTERN = re.compile(
    r"eyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}"
    r"|sb_secret_[A-Za-z0-9_-]+"
    r"|sb_publishable_[A-Za-z0-9_-]+"
    r"|[a-zA-Z][a-zA-Z0-9+.-]*://[^\s/@]+:[^\s/@]+@\S*"
)

_KNOWN_SECRETS: List[str] = []


class BackupError(Exception):
    """A fatal, whole-run setup problem (missing config, cannot list the
    bucket, cannot reach R2 at all). Per-object failures are never raised —
    they are collected into FileResult and reported in the summary instead,
    so one bad object never aborts the rest of the backup."""


@dataclass
class FileResult:
    path: str
    size: int
    ok: bool
    error: Optional[str] = None


def log(message: str) -> None:
    print(message, flush=True)


def _register_secret(value: Optional[str]) -> None:
    """Remembers an actual secret value loaded from the environment so
    scrub_secrets() can redact it verbatim from anything logged afterward —
    stronger than pattern-guessing alone, since it works for whatever the
    real configured value happens to be."""
    value = (value or "").strip()
    if value and value not in _KNOWN_SECRETS:
        _KNOWN_SECRETS.append(value)


def scrub_secrets(text: Optional[str]) -> str:
    result = text or ""
    for secret in sorted(_KNOWN_SECRETS, key=len, reverse=True):
        result = result.replace(secret, "[REDACTED]")
    result = _SECRET_PATTERN.sub("[REDACTED]", result)
    return result.strip()


def load_r2_config() -> Dict[str, str]:
    """Missing R2 configuration is the one setup failure this shares with
    scripts/backup_database.py — same variables, same fail-fast behavior,
    never logs a value."""
    missing = [name for name in REQUIRED_R2_ENV_VARS if not os.getenv(name, "").strip()]
    if missing:
        raise BackupError(f"Missing required environment variable(s): {', '.join(missing)}")
    config = {name: os.environ[name].strip() for name in REQUIRED_R2_ENV_VARS}
    _register_secret(config["R2_ACCESS_KEY_ID"])
    _register_secret(config["R2_SECRET_ACCESS_KEY"])
    return config


def get_supabase_bucket_client() -> Tuple[Any, str]:
    """Returns (bucket_client, bucket_name). Raises BackupError (never
    logging the key itself) if Supabase isn't configured with a genuine
    service-role-capable key — see backend/supabase_client.py, reused
    as-is and not duplicated here."""
    from supabase_client import SupabaseConfigurationError, get_supabase_client, get_supabase_settings

    try:
        settings = get_supabase_settings(required=True)
        client = get_supabase_client(required=True)
    except SupabaseConfigurationError as exc:
        raise BackupError(f"Supabase is not configured correctly: {exc}") from exc
    if settings is None or client is None:
        raise BackupError("Supabase is not configured correctly: no client could be created.")

    _register_secret(settings.secret_key)
    bucket_name = os.getenv("SUPABASE_STORAGE_BUCKET", "").strip() or DEFAULT_SUPABASE_BUCKET
    return client.storage.from_(bucket_name), bucket_name


def is_folder_entry(entry: Dict[str, Any]) -> bool:
    """Supabase Storage's list() convention: a pseudo-folder placeholder
    entry has id=None (and metadata=None); a real object always has a
    non-null id."""
    return entry.get("id") is None


def iter_all_objects(bucket_client, prefix: str = "") -> Iterator[Dict[str, Any]]:
    """Recursively walks Supabase Storage's pseudo-folder tree — list()
    only ever returns one folder level per call — and yields real object
    entries only (folder placeholders are recursed into, never yielded),
    each augmented with a 'full_path' key holding the exact original
    nested path + filename. Paginates within each folder level in case it
    holds more than LIST_PAGE_SIZE entries."""
    offset = 0
    while True:
        page = bucket_client.list(path=prefix, options={"limit": LIST_PAGE_SIZE, "offset": offset}) or []
        for entry in page:
            name = entry.get("name")
            if not name:
                continue
            full_path = f"{prefix}{name}"
            if is_folder_entry(entry):
                yield from iter_all_objects(bucket_client, f"{full_path}/")
            else:
                augmented = dict(entry)
                augmented["full_path"] = full_path
                yield augmented
        if len(page) < LIST_PAGE_SIZE:
            break
        offset += LIST_PAGE_SIZE


def r2_object_key_for(supabase_bucket: str, object_path: str) -> str:
    """storage/<supabase bucket>/<original object path> — the exact
    original path and filename, never altered."""
    return f"storage/{supabase_bucket}/{object_path}"


def build_r2_client(access_key_id: str, secret_access_key: str, endpoint_url: str):
    """R2 is S3-compatible; boto3 is already a required dependency (used by
    both the existing optional S3 upload-storage backend and
    scripts/backup_database.py) — no new dependency was needed here."""
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


def upload_and_verify(r2_client, bucket: str, object_key: str, data: bytes) -> None:
    """Only after this succeeds may an object be counted as backed up: a
    HEAD request confirming the object exists in R2 with the same byte
    size as what was just downloaded from Supabase."""
    r2_client.put_object(Bucket=bucket, Key=object_key, Body=data)
    head = r2_client.head_object(Bucket=bucket, Key=object_key)
    remote_size = head.get("ContentLength")
    if remote_size != len(data):
        raise BackupError(f"Uploaded object size mismatch (local={len(data)} bytes, remote={remote_size}).")


def backup_one_object(bucket_client, r2_client, r2_bucket: str, supabase_bucket_name: str, object_path: str) -> FileResult:
    """Never raises — any failure for this one object is captured into the
    returned FileResult so the loop in main() can continue backing up
    everything else."""
    try:
        data = bucket_client.download(object_path)
        if not isinstance(data, (bytes, bytearray)):
            raise BackupError("Supabase Storage returned an invalid download payload.")
        data = bytes(data)
        object_key = r2_object_key_for(supabase_bucket_name, object_path)
        upload_and_verify(r2_client, r2_bucket, object_key, data)
        return FileResult(path=object_path, size=len(data), ok=True)
    except Exception as exc:  # noqa: BLE001 - one bad object must never abort the run
        return FileResult(path=object_path, size=0, ok=False, error=scrub_secrets(str(exc))[:500])


def main() -> int:
    log("Starting Cauldra Supabase Storage backup...")

    try:
        r2_config = load_r2_config()
    except BackupError as exc:
        log(f"Backup failed: {exc}")
        return 1

    try:
        bucket_client, supabase_bucket_name = get_supabase_bucket_client()
    except BackupError as exc:
        log(f"Backup failed: {exc}")
        return 1

    try:
        r2_client = build_r2_client(r2_config["R2_ACCESS_KEY_ID"], r2_config["R2_SECRET_ACCESS_KEY"], r2_config["R2_ENDPOINT"])
    except Exception as exc:
        log(f"Backup failed: could not create the R2 client ({exc.__class__.__name__}).")
        return 1

    try:
        objects = list(iter_all_objects(bucket_client))
    except Exception as exc:
        log(f"Backup failed: could not list Supabase Storage objects ({scrub_secrets(str(exc))[:500]}).")
        return 1

    if not objects:
        log("No files found.")
        log("Backup completed successfully.")
        return 0

    log(f"Discovered {len(objects)} file(s) in Supabase Storage bucket '{supabase_bucket_name}'.")

    results: List[FileResult] = []
    for entry in objects:
        object_path = entry["full_path"]
        result = backup_one_object(bucket_client, r2_client, r2_config["R2_BACKUP_BUCKET"], supabase_bucket_name, object_path)
        results.append(result)
        if result.ok:
            log(f"Backed up: {r2_object_key_for(supabase_bucket_name, object_path)}")
        else:
            log(f"Failed: {object_path} ({result.error})")

    succeeded = [r for r in results if r.ok]
    failed = [r for r in results if not r.ok]
    total_bytes = sum(r.size for r in succeeded)

    log("Storage backup summary:")
    log(f"  files discovered: {len(results)}")
    log(f"  files uploaded:   {len(succeeded)}")
    log(f"  files failed:     {len(failed)}")
    log(f"  total bytes backed up: {total_bytes}")

    if failed:
        log("Backup completed with failures.")
        return 1

    log("Backup completed successfully.")
    return 0


if __name__ == "__main__":
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass
    sys.exit(main())
