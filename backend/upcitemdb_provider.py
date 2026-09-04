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

# Diagnostic tracing (default on). Set UPCITEMDB_DEBUG=0 to silence. These logs
# are secret-safe BY CONSTRUCTION: they only ever print the barcode, the PUBLIC
# endpoint URL, whether (not which) an auth header is attached, UPCitemdb's own
# response, and exception types/messages — never request headers, the API key,
# Authorization, tokens, or cookies.
_UPC_VERBOSE = os.getenv("UPCITEMDB_DEBUG", "1").strip().lower() not in ("0", "false", "no", "off")
_BODY_PREVIEW_CHARS = 300


def _upc_log(msg: str) -> None:
    if _UPC_VERBOSE:
        print(msg)

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
    outcome: str                        # "hit" | "miss" | "temporary_error" | "config_error"
    identity: Optional[UpcItemIdentity]
    detail: str
    http_status: Optional[int]


def lookup_upcitemdb_detailed(barcode: str) -> UpcLookupResult:
    """Look up one already-normalized barcode against UPCitemdb and report WHY
    the lookup ended the way it did. Never raises. Never logs the API key or
    request headers.

      outcome="hit"              identity is a sanitized dict
      outcome="miss"             the provider answered normally but no product
                                 exists for this barcode
      outcome="temporary_error"  the provider could not be reached or could not
                                 give a usable answer right now: timeout, DNS,
                                 SSL, connection reset, HTTP 429 (rate limit),
                                 HTTP 5xx, other 4xx, TOO_FAST burst limit,
                                 non-JSON body, or a response whose schema no
                                 longer matches the parser
      outcome="config_error"     the provider is misconfigured / credentials
                                 are missing or rejected: UPCITEMDB_PLAN=paid
                                 with an empty UPCITEMDB_API_KEY, or HTTP 401 /
                                 HTTP 403 from UPCitemdb

    main.py maps hit->source"upcitemdb", miss->source"not_found",
    temporary_error/config_error->source"upcitemdb_unavailable" (never
    "not_found"), and logs config_error loudly server-side.
    """
    def _r(outcome: str, identity, detail: str, http_status=None) -> "UpcLookupResult":
        return {"outcome": outcome, "identity": identity, "detail": detail, "http_status": http_status}

    _upc_log(f"[upcitemdb] barcode: {barcode!r}")
    if not barcode:
        _upc_log("[upcitemdb] parse result: MISS (empty barcode)")
        return _r("miss", None, "empty barcode")

    url, headers = _provider_url_and_headers()
    auth_desc = "user_key header attached" if (UPCITEMDB_PLAN == "paid" and headers.get("user_key")) else "none"
    _upc_log(f"[upcitemdb] request endpoint: {url}  (mode={UPCITEMDB_PLAN}, auth={auth_desc})")

    if UPCITEMDB_PLAN == "paid" and not UPCITEMDB_API_KEY:
        _upc_log("[upcitemdb] parse result: CONFIG_ERROR (UPCITEMDB_PLAN=paid but UPCITEMDB_API_KEY is empty)")
        print("[upcitemdb] CONFIG ERROR: UPCITEMDB_PLAN=paid but UPCITEMDB_API_KEY is not set")
        return _r("config_error", None, "UPCITEMDB_PLAN=paid but UPCITEMDB_API_KEY is not set")

    import requests  # imported lazily, matching this codebase's other external-API call sites

    _upc_log("[upcitemdb] request started")
    try:
        resp = requests.get(url, params={"upc": barcode}, headers=headers, timeout=_REQUEST_TIMEOUT_SECONDS)
    except requests.exceptions.Timeout as exc:
        _upc_log(f"[upcitemdb] exception: {type(exc).__name__}: {exc}")
        _upc_log("[upcitemdb] parse result: TEMPORARY_ERROR (timeout)")
        return _r("temporary_error", None, f"timeout after {_REQUEST_TIMEOUT_SECONDS}s")
    except requests.exceptions.SSLError as exc:
        _upc_log(f"[upcitemdb] exception: {type(exc).__name__}: {exc}")
        _upc_log("[upcitemdb] parse result: TEMPORARY_ERROR (SSL error)")
        return _r("temporary_error", None, f"SSL error: {type(exc).__name__}")
    except requests.exceptions.ConnectionError as exc:
        # DNS failure, connection refused, connection reset all arrive here.
        _upc_log(f"[upcitemdb] exception: {type(exc).__name__}: {exc}")
        _upc_log("[upcitemdb] parse result: TEMPORARY_ERROR (connection / DNS)")
        return _r("temporary_error", None, f"connection/DNS error: {type(exc).__name__}")
    except requests.exceptions.RequestException as exc:
        _upc_log(f"[upcitemdb] exception: {type(exc).__name__}: {exc}")
        _upc_log("[upcitemdb] parse result: TEMPORARY_ERROR (request exception)")
        return _r("temporary_error", None, f"request error: {type(exc).__name__}")

    status = resp.status_code
    _upc_log(f"[upcitemdb] HTTP status: {status}")
    _rl = {k: resp.headers.get(k) for k in ("X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset", "Retry-After")}
    if any(_rl.values()):
        _upc_log(
            f"[upcitemdb] rate limit headers: limit={_rl['X-RateLimit-Limit']} "
            f"remaining={_rl['X-RateLimit-Remaining']} reset={_rl['X-RateLimit-Reset']} "
            f"retry_after={_rl['Retry-After']}"
        )
    _body = resp.text or ""
    _upc_log("[upcitemdb] response body preview: " + _body[:_BODY_PREVIEW_CHARS].replace("\n", " ").replace("\r", " "))

    if status in (401, 403):
        _upc_log(f"[upcitemdb] parse result: CONFIG_ERROR (HTTP {status} - authentication rejected)")
        print(f"[upcitemdb] CONFIG ERROR: UPCitemdb rejected the request with HTTP {status} "
              f"(mode={UPCITEMDB_PLAN}; check UPCITEMDB_PLAN / UPCITEMDB_API_KEY)")
        return _r("config_error", None, f"HTTP {status} (authentication rejected)", status)
    if status == 429:
        _upc_log("[upcitemdb] parse result: TEMPORARY_ERROR (HTTP 429 rate limit)")
        return _r("temporary_error", None, "HTTP 429 (rate limited)", status)
    if status == 404:
        _upc_log("[upcitemdb] parse result: MISS (HTTP 404)")
        return _r("miss", None, "HTTP 404", status)
    if 500 <= status <= 599:
        _upc_log(f"[upcitemdb] parse result: TEMPORARY_ERROR (HTTP {status} provider server error)")
        return _r("temporary_error", None, f"HTTP {status} (provider server error)", status)
    if not resp.ok:
        _upc_log(f"[upcitemdb] parse result: TEMPORARY_ERROR (HTTP {status})")
        return _r("temporary_error", None, f"HTTP {status}", status)

    try:
        payload = resp.json()
    except ValueError:
        _upc_log("[upcitemdb] parse result: TEMPORARY_ERROR (response body was not valid JSON)")
        return _r("temporary_error", None, "invalid JSON body", status)

    if not isinstance(payload, dict):
        _upc_log("[upcitemdb] parse result: TEMPORARY_ERROR (top-level JSON was not an object)")
        return _r("temporary_error", None, "top-level JSON not an object", status)

    code = payload.get("code")
    _upc_log(f"[upcitemdb] response code field: {code!r}  total={payload.get('total')!r}")

    if code == "TOO_FAST":
        _upc_log("[upcitemdb] parse result: TEMPORARY_ERROR (TOO_FAST burst limit on HTTP 200)")
        return _r("temporary_error", None, "TOO_FAST (burst limit) on HTTP 200", status)
    if code in ("NOT_FOUND", "INVALID_UPC", "INVALID_QUERY"):
        _upc_log(f"[upcitemdb] parse result: MISS (code={code})")
        return _r("miss", None, f"code={code}", status)
    if code not in ("OK", None):
        _upc_log(f"[upcitemdb] parse result: TEMPORARY_ERROR (unexpected code={code!r})")
        return _r("temporary_error", None, f"unexpected code={code}", status)

    items = payload.get("items")
    if not isinstance(items, list):
        _upc_log("[upcitemdb] parse result: TEMPORARY_ERROR ('items' missing / not a list - schema mismatch)")
        return _r("temporary_error", None, "'items' missing or not a list (schema mismatch)", status)
    if not items:
        _upc_log("[upcitemdb] parse result: MISS (items list empty)")
        return _r("miss", None, "no items in response", status)
    if not isinstance(items[0], dict):
        _upc_log("[upcitemdb] parse result: TEMPORARY_ERROR (items[0] not an object - schema mismatch)")
        return _r("temporary_error", None, "items[0] not an object (schema mismatch)", status)

    identity = _sanitize_item(barcode, items[0])
    if identity is None:
        _upc_log("[upcitemdb] parse result: MISS (item carried no usable product title)")
        return _r("miss", None, "item had no usable product title", status)

    _upc_log(f"[upcitemdb] parse result: HIT ({identity['product_name'][:60]!r})")
    return _r("hit", identity, "ok", status)


def lookup_upcitemdb(barcode: str) -> Optional[UpcItemIdentity]:
    """Backwards-compatible thin wrapper: a sanitized identity dict on a
    confident single-item match, None for every other outcome. Callers that
    must tell "unknown barcode" apart from "provider unavailable" should call
    lookup_upcitemdb_detailed() instead."""
    return lookup_upcitemdb_detailed(barcode)["identity"]
