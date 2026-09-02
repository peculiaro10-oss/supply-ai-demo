"""Private upload storage providers.

The database continues to own upload metadata and authorization. Providers only
store opaque object keys, so moving from local disk to object storage does not
change the browser workflow or make uploads public.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable, Optional

from supabase_client import get_supabase_client, get_supabase_settings


def _validate_object_key(key: str) -> str:
    """Accept only relative, traversal-free object keys on every provider."""
    raw = str(key or "")
    if raw.startswith(("/", "\\")):
        raise ValueError("Invalid upload storage key")
    normalized = raw.replace("\\", "/").strip("/")
    parts = normalized.split("/") if normalized else []
    if not parts or ":" in parts[0] or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("Invalid upload storage key")
    return "/".join(parts)


class StorageProvider:
    name = "unknown"

    def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        raise NotImplementedError

    def get_path(self, key: str) -> Optional[Path]:
        """Return a local path when one exists; object providers return None."""
        return None

    def read_bytes(self, key: str) -> bytes:
        raise NotImplementedError

    def delete(self, key: str) -> None:
        raise NotImplementedError


class LocalStorage(StorageProvider):
    name = "local"

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self.root / _validate_object_key(key)).resolve()
        if self.root not in path.parents:
            raise ValueError("Invalid upload storage key")
        return path

    def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        try:
            temporary.write_bytes(data)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def get_path(self, key: str) -> Optional[Path]:
        path = self._path(key)
        return path if path.is_file() else None

    def read_bytes(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)


class S3CompatibleStorage(StorageProvider):
    """Private S3-compatible storage, enabled only through explicit config."""
    name = "s3"

    def __init__(self, bucket: str, prefix: str = "", endpoint_url: Optional[str] = None, region: Optional[str] = None):
        try:
            import boto3
        except ImportError as exc:
            raise RuntimeError("S3 storage requires the optional boto3 dependency.") from exc
        if not bucket:
            raise RuntimeError("SUPPLY_AI_STORAGE_BUCKET is required for S3 storage.")
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self.client = boto3.client("s3", endpoint_url=endpoint_url or None, region_name=region or None)

    def _object_key(self, key: str) -> str:
        key = _validate_object_key(key)
        return f"{self.prefix}/{key}" if self.prefix else key

    def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        self.client.put_object(Bucket=self.bucket, Key=self._object_key(key), Body=data, ContentType=content_type)

    def read_bytes(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=self._object_key(key))["Body"].read()

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=self._object_key(key))


class SupabaseStorage(StorageProvider):
    """Private Supabase Storage accessed only with the backend secret key."""
    name = "supabase"

    def __init__(self, bucket: str, client_factory: Callable[[], Any] = get_supabase_client):
        if not bucket:
            raise RuntimeError("SUPABASE_STORAGE_BUCKET is required for Supabase storage.")
        self.bucket = bucket
        self._client_factory = client_factory

    def _bucket_client(self):
        return self._client_factory().storage.from_(self.bucket)

    def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        self._bucket_client().upload(
            path=_validate_object_key(key),
            file=data,
            file_options={"content-type": content_type, "upsert": "false"},
        )

    def read_bytes(self, key: str) -> bytes:
        data = self._bucket_client().download(_validate_object_key(key))
        if not isinstance(data, (bytes, bytearray)):
            raise RuntimeError("Supabase Storage returned an invalid download payload.")
        return bytes(data)

    def delete(self, key: str) -> None:
        self._bucket_client().remove([_validate_object_key(key)])


def build_storage_provider(root: Path) -> StorageProvider:
    backend = os.getenv("SUPPLY_AI_STORAGE_BACKEND", "local").strip().lower()
    if backend == "local":
        return LocalStorage(root)
    if backend == "s3":
        return S3CompatibleStorage(
            bucket=os.getenv("SUPPLY_AI_STORAGE_BUCKET", "").strip(),
            prefix=os.getenv("SUPPLY_AI_STORAGE_PREFIX", "").strip(),
            endpoint_url=os.getenv("SUPPLY_AI_STORAGE_ENDPOINT", "").strip(),
            region=os.getenv("SUPPLY_AI_STORAGE_REGION", "").strip(),
        )
    if backend == "supabase":
        settings = get_supabase_settings(required=True)
        return SupabaseStorage(settings.storage_bucket or "")
    raise RuntimeError("SUPPLY_AI_STORAGE_BACKEND must be local, s3, or supabase.")
