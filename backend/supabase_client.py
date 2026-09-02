"""Server-only Supabase client configuration.

Cauldra's browser must never receive the key loaded here. The existing
FastAPI authentication and tenant checks remain the public security boundary;
this client is only used by trusted backend code for Supabase services such as
private object storage and explicitly reviewed Data API/RPC calls.
"""
from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Optional
from urllib.parse import urlparse


class SupabaseConfigurationError(RuntimeError):
    """Raised for missing or unsafe server-side Supabase configuration."""


def _legacy_jwt_role(value: str) -> Optional[str]:
    """Read the unverified role claim only to reject an accidental anon key."""
    parts = value.split(".")
    if len(parts) != 3:
        return None
    try:
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        decoded = base64.urlsafe_b64decode(payload.encode("ascii"))
        data = json.loads(decoded.decode("utf-8"))
    except (ValueError, UnicodeError, json.JSONDecodeError):
        return None
    role = data.get("role")
    return str(role) if role is not None else None


@dataclass(frozen=True)
class SupabaseSettings:
    url: str
    secret_key: str
    storage_bucket: Optional[str] = None

    @classmethod
    def from_environment(cls, *, required: bool = False) -> Optional["SupabaseSettings"]:
        url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
        secret_key = (
            os.getenv("SUPABASE_SECRET_KEY", "").strip()
            or os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        )
        bucket = os.getenv("SUPABASE_STORAGE_BUCKET", "").strip() or None

        if not url and not secret_key:
            if required:
                raise SupabaseConfigurationError(
                    "SUPABASE_URL and SUPABASE_SECRET_KEY (or the legacy "
                    "SUPABASE_SERVICE_ROLE_KEY) are required."
                )
            return None
        if not url or not secret_key:
            raise SupabaseConfigurationError(
                "SUPABASE_URL and a server-side Supabase secret key must be configured together."
            )

        parsed = urlparse(url)
        local_host = (parsed.hostname or "").lower() in {"localhost", "127.0.0.1", "::1"}
        allowed_schemes = {"http", "https"} if local_host else {"https"}
        if not parsed.hostname or parsed.scheme not in allowed_schemes:
            raise SupabaseConfigurationError(
                "SUPABASE_URL must be an HTTPS project URL (HTTP is allowed only locally)."
            )

        if secret_key.startswith(("sb_publishable_", "sb_anon_")):
            raise SupabaseConfigurationError(
                "A publishable/anon Supabase key cannot be used by the trusted backend."
            )
        lowered_key = secret_key.lower()
        if any(marker in lowered_key for marker in ("replace", "placeholder", "your_", "example")):
            raise SupabaseConfigurationError(
                "The configured Supabase secret key is still a placeholder."
            )
        legacy_role = _legacy_jwt_role(secret_key)
        if legacy_role and legacy_role != "service_role":
            raise SupabaseConfigurationError(
                "The configured legacy Supabase key is not a service_role key."
            )
        if secret_key.startswith("sb_secret_") and len(secret_key) < 32:
            raise SupabaseConfigurationError(
                "The configured sb_secret_ Supabase key is incomplete."
            )
        if not secret_key.startswith("sb_secret_") and legacy_role != "service_role":
            raise SupabaseConfigurationError(
                "SUPABASE_SECRET_KEY must contain an sb_secret_ key or a legacy service_role JWT."
            )

        return cls(url=url, secret_key=secret_key, storage_bucket=bucket)


@lru_cache(maxsize=1)
def get_supabase_settings(*, required: bool = False) -> Optional[SupabaseSettings]:
    return SupabaseSettings.from_environment(required=required)


@lru_cache(maxsize=1)
def get_supabase_client(*, required: bool = True) -> Any:
    settings = get_supabase_settings(required=required)
    if settings is None:
        return None
    try:
        from supabase import create_client
    except ImportError as exc:
        raise SupabaseConfigurationError(
            "Supabase is configured but the 'supabase' Python package is not installed."
        ) from exc
    return create_client(settings.url, settings.secret_key)


def clear_supabase_client_cache() -> None:
    """Test/deployment helper for re-reading rotated environment secrets."""
    get_supabase_client.cache_clear()
    get_supabase_settings.cache_clear()
