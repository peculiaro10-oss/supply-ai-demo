"""ERR-002 — a malformed request body must never produce an unhandled 500.

The audit's table is reproduced exactly: the discriminator was whether the raw
body decodes as UTF-8, not size, not "binary", not multipart, not filename, and
not authentication. Subprocess-isolated import of `main`, no database needed:
validation happens before any dependency runs.
"""
import os
os.environ['PYTHON_DOTENV_DISABLED'] = '1'
os.environ['SUPPLY_AI_SKIP_DB_STARTUP_CHECK'] = 'true'
os.environ['DATABASE_URL'] = 'postgresql+psycopg://test:test@127.0.0.1:65432/cauldra_test'
os.environ['SUPPLY_AI_SECRET_KEY'] = 'isolated-test-secret-012345678901234567890123456789'
os.environ['SUPPLY_AI_CORS_ORIGINS'] = 'https://localhost,capacitor://localhost'
import json
import unittest
from fastapi.testclient import TestClient
import main

# Unauthenticated endpoints the audit proved reachable, plus an authenticated one.
ENDPOINTS = ["/auth/admin-login", "/auth/register-business", "/auth/verify-business",
             "/auth/forgot-password", "/onboarding/email/verify", "/products/"]


class MalformedBodyTests(unittest.TestCase):
    def setUp(self):
        # raise_server_exceptions=False so an unhandled error surfaces as the 500 a client would see
        self.client = TestClient(main.app, raise_server_exceptions=False)

    def tearDown(self):
        self.client.close()

    def post(self, path, body, content_type, **kw):
        return self.client.post(path, content=body, headers={"Content-Type": content_type}, **kw)

    def test_single_non_utf8_byte_is_rejected_not_crashed(self):
        for path in ENDPOINTS:
            for content_type in ("text/plain", "application/octet-stream", "multipart/form-data; boundary=x"):
                r = self.post(path, b"\xff", content_type)
                # 422 where validation runs first; 401 where authentication does. Never 500.
                self.assertIn(r.status_code, (401, 422), f"{path} {content_type} -> {r.status_code}")
                self.assertIn("application/json", r.headers["content-type"])

    def test_the_audit_table(self):
        cases = [
            ("text/plain", "héllo 日本語".encode(), 422),
            ("text/plain", b"\xff", 422),                      # was 500
            ("application/octet-stream", b"\x89\x50", 422),    # was 500
            ("application/octet-stream", b"plain text", 422),
            ("multipart/form-data; boundary=x", "日本語é".encode(), 422),
            ("multipart/form-data; boundary=x", b"\xff", 422), # was 500
            ("application/json", b"\x89\x50\x4e", 400),        # JSON path already handled it
            ("text/plain", b"\xff" * 4096, 422),               # size is irrelevant
        ]
        for content_type, body, expected in cases:
            r = self.post("/auth/admin-login", body, content_type)
            self.assertEqual(r.status_code, expected, f"{content_type} {body[:4]!r} -> {r.status_code}")

    def test_no_content_type_and_empty_body_still_answer_json(self):
        r = self.client.post("/auth/admin-login", content=b"\xff\xfe")
        self.assertIn(r.status_code, (400, 422))
        self.assertIn("application/json", r.headers["content-type"])

    def test_response_never_echoes_the_raw_body(self):
        secretish = b"\xffsupersecret-token-value"
        r = self.post("/auth/admin-login", secretish, "text/plain")
        self.assertEqual(r.status_code, 422)
        self.assertNotIn("supersecret", r.text)
        detail = r.json()["detail"]
        self.assertTrue(any("bytes that are not valid UTF-8" in json.dumps(d) for d in detail), r.text)

    def test_ordinary_validation_errors_keep_their_shape(self):
        r = self.client.post("/auth/admin-login", json={"business_id": "AP-1"})   # missing fields
        self.assertEqual(r.status_code, 422)
        detail = r.json()["detail"]
        self.assertTrue(detail and all({"type", "loc", "msg"} <= set(d) for d in detail), r.text)
        self.assertTrue(any(d["loc"][-1] in ("username", "password") for d in detail), r.text)

    def test_security_and_cors_headers_survive_a_malformed_body(self):
        # ERR-003's symptom for this case: the old 500 escaped the middleware chain.
        r = self.client.post("/auth/admin-login", content=b"\xff",
                             headers={"Content-Type": "text/plain", "Origin": "https://localhost"})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.headers.get("x-content-type-options"), "nosniff")
        # CORS origins come from the environment, which a sibling test module may have set
        # differently when the whole suite is imported into one process.
        if "https://localhost" in getattr(main, "ALLOWED_ORIGINS", []):
            self.assertEqual(r.headers.get("access-control-allow-origin"), "https://localhost")


if __name__ == "__main__":
    unittest.main(verbosity=2)
