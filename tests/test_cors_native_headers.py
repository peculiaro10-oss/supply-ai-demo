"""NATIVE-PAY-002: the packaged app (origin https://localhost) calls the API
cross-origin, so every custom request header it sends must be in the CORS
allow-list, or the browser's preflight is refused ("Disallowed CORS headers")
and the request never reaches the server. payments.js sends Idempotency-Key on
every Paystack initialisation; with it missing, no native checkout, upgrade,
trial or payment-method update could start (combined offline pass, OX-L-04).

Reads the real allow-list from backend/main.py (importing main needs a
database), then runs a real Starlette CORS preflight with it."""
import ast
import pathlib
import re

from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _cors_kwargs():
    tree = ast.parse((ROOT / "backend" / "main.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "add_middleware"
                and node.args and getattr(node.args[0], "id", None) == "CORSMiddleware"):
            return {kw.arg: ast.literal_eval(kw.value) for kw in node.keywords
                    if kw.arg in ("allow_methods", "allow_headers", "expose_headers", "allow_credentials")}
    raise AssertionError("CORSMiddleware registration not found in backend/main.py")


def _frontend_custom_headers():
    """Header names the frontend sets itself (bracket assignment or object keys)."""
    names = set()
    for path in (ROOT / "frontend" / "js").glob("*.js"):
        text = path.read_text(encoding="utf-8")
        names.update(re.findall(r"\[\s*['\"]((?:Idempotency-Key|X-[A-Za-z0-9-]+))['\"]\s*\]\s*=", text))
        names.update(re.findall(r"['\"]((?:Idempotency-Key|X-Cauldra-[A-Za-z0-9-]+))['\"]\s*:", text))
    return names


def _preflight(headers):
    kwargs = _cors_kwargs()
    app = Starlette(routes=[Route("/subscription/payment-method/init", lambda r: PlainTextResponse("ok"), methods=["POST"])])
    app.add_middleware(CORSMiddleware, allow_origins=["https://localhost"], **kwargs)
    return TestClient(app).options("/subscription/payment-method/init", headers={
        "Origin": "https://localhost", "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": headers})


def test_idempotency_key_is_allowed_cross_origin():
    assert "Idempotency-Key" in _frontend_custom_headers(), "payments.js no longer sends Idempotency-Key?"
    r = _preflight("authorization,content-type,idempotency-key")
    assert r.status_code == 200, r.text


def test_every_custom_frontend_header_is_allowed():
    allowed = {h.lower() for h in _cors_kwargs()["allow_headers"]}
    missing = sorted(h for h in _frontend_custom_headers() if h.lower() not in allowed)
    assert not missing, f"frontend sends headers the CORS allow-list refuses: {missing}"


def test_unlisted_header_is_still_refused():
    # The fix widens the list by the header the app needs, not to "*".
    r = _preflight("authorization,x-not-a-cauldra-header")
    assert r.status_code == 400
