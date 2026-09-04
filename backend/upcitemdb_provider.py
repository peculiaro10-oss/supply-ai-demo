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


class UpcLookupResult(TypedDict):
    outcome: str                        # "hit" | "miss" | "rate_limited" | "error"
    identity: Optional[UpcItemIdentity]
    detail: str


def lookup_upcitemdb_detailed(barcode: str) -> UpcLookupResult:
    """Look up one already-normalized barcode against UPCitemdb and report WHY
    the lookup ended the way it did.

    The plain lookup_upcitemdb() below returns dict-or-None, which makes a
    genuine "this barcode is unknown" indistinguishable from "the request
    timed out / was rate limited / the provider 500'd". /catalog/barcode-lookup
    needs that distinction so it can log a real trace and so it never tells the
    user "not in the Cauldra catalog" when the catalog was never the problem.

      outcome="hit"           identity is a sanitized dict
      outcome="miss"          barcode genuinely not in UPCitemdb (or empty input)
      outcome="rate_limited"  HTTP 429, or the trial endpoint's TOO_FAST body
      outcome="error"         timeout / unreachable / HTTP 4xx-5xx / bad JSON /
                              paid plan with no key

    Never raises. Never logs the API key.
    """
    def _r(outcome: str, identity, detail: str) -> "UpcLookupResult":
        return {"outcome": outcome, "identity": identity, "detail": detail}

    if not barcode:
        return _r("miss", None, "no barcode")

    url, headers = _provider_url_and_headers()
    # Provider mode is logged (never the key or headers) so a developer can see
    # which endpoint/auth path a lookup took.
    print(f"[upcitemdb] lookup (mode={UPCITEMDB_PLAN})")
    if UPCITEMDB_PLAN == "paid" and not UPCITEMDB_API_KEY:
        # Misconfiguration, not a provider failure — fail closed rather than
        # sending an unauthenticated request to the paid endpoint.
        print("[upcitemdb] UPCITEMDB_PLAN=paid but UPCITEMDB_API_KEY is not set; skipping lookup.")
        return _r("error", None, "misconfigured: UPCITEMDB_PLAN=paid without UPCITEMDB_API_KEY")

    import requests  # imported lazily, matching this codebase's other external-API call sites

    try:
        resp = requests.get(url, params={"upc": barcode}, headers=headers, timeout=_REQUEST_TIMEOUT_SECONDS)
    except requests.exceptions.Timeout:
        print("[upcitemdb] lookup timed out")
        return _r("error", None, f"timeout after {_REQUEST_TIMEOUT_SECONDS}s")
    except requests.exceptions.RequestException as exc:
        print(f"[upcitemdb] lookup unreachable: {type(exc).__name__}")
        return _r("error", None, f"unreachable: {type(exc).__name__}")

    if resp.status_code == 429:
        print("[upcitemdb] rate limit exceeded (429) — falling back to manual entry, not retrying")
        return _r("rate_limited", None, "HTTP 429")
    if resp.status_code == 404:
        print("[upcitemdb] barcode not found (HTTP 404)")
        return _r("miss", None, "HTTP 404")
    if not resp.ok:
        print(f"[upcitemdb] lookup failed: HTTP {resp.status_code}")
        return _r("error", None, f"HTTP {resp.status_code}")

    try:
        payload = resp.json()
    except ValueError:
        print("[upcitemdb] lookup returned invalid JSON")
        return _r("error", None, "invalid JSON body")

    if not isinstance(payload, dict):
        print("[upcitemdb] malformed response (top-level JSON was not an object)")
        return _r("error", None, "top-level JSON not an object")
    # UPCitemdb's trial endpoint signals its burst limit as an HTTP 200 with
    # this body code, not a 429 status — treated exactly like the real 429.
    if payload.get("code") == "TOO_FAST":
        print("[upcitemdb] rate limit exceeded (TOO_FAST) — falling back to manual entry, not retrying")
        return _r("rate_limited", None, "TOO_FAST body on HTTP 200")
    items = payload.get("items")
    if isinstance(items, list) and not items:
        print("[upcitemdb] barcode not found (no items in response)")
        return _r("miss", None, "no items in response")
    if not isinstance(items, list) or not isinstance(items[0], dict):
        print("[upcitemdb] malformed response (unexpected 'items' shape)")
        return _r("error", None, "unexpected 'items' shape")
    identity = _sanitize_item(barcode, items[0])
    if identity is None:
        print("[upcitemdb] malformed item (no usable product title)")
        return _r("miss", None, "item had no usable product title")
    return _r("hit", identity, "ok")


def lookup_upcitemdb(barcode: str) -> Optional[UpcItemIdentity]:
    """Backwards-compatible thin wrapper: a sanitized identity dict on a
    confident single-item match, None for every other outcome. Callers that
    must tell "unknown barcode" apart from "provider unavailable" should call
    lookup_upcitemdb_detailed() instead."""
    return lookup_upcitemdb_detailed(barcode)["identity"]
