"""UPCitemdb barcode-lookup provider.

Isolated from main.py on purpose: this is the ONLY place that knows how to
talk to UPCitemdb specifically (its two endpoints, its auth headers, its
response shape, its error codes). main.py's /catalog/barcode-lookup route
calls lookup_upcitemdb() and gets back either a small sanitized identity
dict or None — it never sees a raw UPCitemdb payload, an HTTP status code,
or an exception. Swapping to a different barcode-data provider later means
adding a sibling module with the same lookup_upcitemdb(barcode) -> dict|None
contract and changing one import in main.py — the Add Product workflow,
General Catalog caching, and the /catalog/barcode-lookup API contract all
stay exactly as they are.

Plan selection (env-driven, never hard-coded):
  UPCITEMDB_PLAN=free (default) -> https://api.upcitemdb.com/prod/trial/lookup
      No signup, no API key, no auth headers. Free Explorer plan: max 100
      requests/day — UPCitemdb enforces this PER SOURCE IP, not as one pool
      shared across every trial user (that per-application accounting only
      applies to paid plans, which authenticate with a key). It also has its
      own short-window burst limit, signaled either via a plain HTTP 429 or
      an HTTP 200 body with `"code": "TOO_FAST"` (see the two checks below —
      both are treated identically: fall back to manual entry, never retry).
      See main.py's General-Catalog-first caching (lookup_general_catalog /
      upsert_general_catalog_identity), which is what keeps Cauldra's actual
      UPCitemdb usage far under either limit regardless of how it's counted.
  UPCITEMDB_PLAN=paid       -> https://api.upcitemdb.com/prod/v1/lookup
      Requires UPCITEMDB_API_KEY, sent as the `user_key` header alongside a
      fixed `key_type: 3scale` header. The key lives ONLY in the server
      environment: never hard-coded here, never logged (including in error
      messages below), never returned in any API response, never sent to
      the frontend.

Only the `lookup` endpoint is used (an exact single-barcode identity fetch),
never UPCitemdb's `search` endpoint — Cauldra has no use for free-text
product search against UPCitemdb, only "what product is this exact
barcode".
"""
from __future__ import annotations

import os
from typing import Optional, TypedDict
from urllib.parse import quote

UPCITEMDB_PLAN = os.getenv("UPCITEMDB_PLAN", "free").strip().lower()
UPCITEMDB_API_KEY = os.getenv("UPCITEMDB_API_KEY", "").strip()

UPCITEMDB_FREE_URL = "https://api.upcitemdb.com/prod/trial/lookup"
UPCITEMDB_PAID_URL = "https://api.upcitemdb.com/prod/v1/lookup"

# Short on purpose: this call happens synchronously inside an interactive
# Add Product barcode scan — a slow/hanging provider must fail fast into the
# existing "enter product details manually" path, not stall the form.
_REQUEST_TIMEOUT_SECONDS = 6

# Fields UPCitemdb may return that Cauldra is explicitly forbidden from
# importing into a business's inventory (see spec: never retail pricing,
# never UPCitemdb's category) — kept here as documentation of what
# _sanitize_item() below must never read, not as an active filter list.
_NEVER_EXTRACT = ("offers", "prices", "lowest_recorded_price", "highest_recorded_price", "category")


class UpcItemIdentity(TypedDict):
    barcode: str
    product_name: str
    brand: Optional[str]
    size: Optional[str]


def _provider_url_and_headers() -> tuple[str, dict]:
    if UPCITEMDB_PLAN == "paid":
        return UPCITEMDB_PAID_URL, {"user_key": UPCITEMDB_API_KEY, "key_type": "3scale"}
    return UPCITEMDB_FREE_URL, {}


def _extract_size(item: dict) -> Optional[str]:
    """UPCitemdb has no dedicated "size" field — dimension/weight are the
    closest optional identity hints it offers (see spec: "model/dimension/
    weight -> optional product identity/size information when useful").
    Never reads pricing or category (see _NEVER_EXTRACT)."""
    for key in ("size", "dimension", "weight"):
        val = item.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()[:120]
    return None


def _sanitize_item(barcode: str, item: dict) -> Optional[UpcItemIdentity]:
    title = (item.get("title") or "").strip()
    if not title:
        return None
    brand = item.get("brand")
    brand = brand.strip() if isinstance(brand, str) and brand.strip() else None
    return {
        "barcode": barcode,
        "product_name": title[:200],
        "brand": brand[:120] if brand else None,
        "size": _extract_size(item),
    }


def lookup_upcitemdb(barcode: str) -> Optional[UpcItemIdentity]:
    """Look up one already-normalized barcode against UPCitemdb.

    Returns a sanitized identity dict (barcode/product_name/brand/size —
    never pricing, never category, never the raw provider payload) on a
    confident single-item match. Returns None for every other outcome —
    barcode not found, invalid request, rate limited, provider/server error,
    timeout, unreachable host, or an unparsable response — so callers always
    have exactly two cases to handle: "got an identity" or "fall through to
    manual entry". This function never raises and never logs the API key.
    """
    if not barcode:
        return None
    url, headers = _provider_url_and_headers()
    if UPCITEMDB_PLAN == "paid" and not UPCITEMDB_API_KEY:
        # Misconfiguration, not a provider failure — fail closed into manual
        # entry rather than sending an unauthenticated request to the paid
        # endpoint (which would just 4xx anyway).
        print("[upcitemdb] UPCITEMDB_PLAN=paid but UPCITEMDB_API_KEY is not set; skipping lookup.")
        return None

    import requests  # imported lazily, matching this codebase's other external-API call sites

    try:
        resp = requests.get(url, params={"upc": barcode}, headers=headers, timeout=_REQUEST_TIMEOUT_SECONDS)
    except requests.exceptions.Timeout:
        print("[upcitemdb] lookup timed out")
        return None
    except requests.exceptions.RequestException as exc:
        print(f"[upcitemdb] lookup unreachable: {type(exc).__name__}")
        return None

    if resp.status_code == 429:
        print("[upcitemdb] rate limit exceeded (429) — falling back to manual entry, not retrying")
        return None
    if resp.status_code == 404:
        return None
    if not resp.ok:
        print(f"[upcitemdb] lookup failed: HTTP {resp.status_code}")
        return None

    try:
        payload = resp.json()
    except ValueError:
        print("[upcitemdb] lookup returned invalid JSON")
        return None

    if not isinstance(payload, dict):
        return None
    # UPCitemdb's trial endpoint signals its burst limit as an HTTP 200 with
    # this body code, not a 429 status — treated exactly like the real 429
    # above: fall back to manual entry, never retry immediately.
    if payload.get("code") == "TOO_FAST":
        print("[upcitemdb] rate limit exceeded (TOO_FAST) — falling back to manual entry, not retrying")
        return None
    items = payload.get("items")
    if not isinstance(items, list) or not items or not isinstance(items[0], dict):
        return None
    return _sanitize_item(barcode, items[0])
