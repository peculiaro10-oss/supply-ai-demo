"""Batch F — ERR-003: an unhandled server error must still reach the browser
as a readable JSON 500 that carries the CORS and security headers, so the
Android WebView (cross-origin from https://localhost) can read it instead of
reporting a connection problem.

Subprocess-isolated import of `main`, no database needed: the probe routes
below raise before touching one.
"""
import os
os.environ['PYTHON_DOTENV_DISABLED'] = '1'
os.environ['SUPPLY_AI_SKIP_DB_STARTUP_CHECK'] = 'true'
os.environ['DATABASE_URL'] = 'postgresql+psycopg://test:test@127.0.0.1:65432/cauldra_test'
os.environ['SUPPLY_AI_SECRET_KEY'] = 'isolated-test-secret-012345678901234567890123456789'
os.environ['SUPPLY_AI_CORS_ORIGINS'] = 'https://localhost,capacitor://localhost'
import unittest
from fastapi import HTTPException
from fastapi.testclient import TestClient
import main

NATIVE_ORIGIN = "https://localhost"


def _boom():
    raise RuntimeError("probe failure with private detail")


def _refused():
    raise HTTPException(status_code=409, detail="Probe conflict.")


main.app.add_api_route("/__batch_f_probe/boom", _boom, methods=["GET", "POST"])
main.app.add_api_route("/__batch_f_probe/refused", _refused, methods=["GET"])


class UnhandledErrorHeadersTests(unittest.TestCase):
    def setUp(self):
        # raise_server_exceptions=False: see the 500 exactly as a client would.
        self.client = TestClient(main.app, raise_server_exceptions=False)

    def tearDown(self):
        self.client.close()

    def test_unhandled_error_is_json_with_cors_and_security_headers(self):
        for method in ("get", "post"):
            r = getattr(self.client, method)("/__batch_f_probe/boom", headers={"Origin": NATIVE_ORIGIN})
            self.assertEqual(r.status_code, 500)
            self.assertIn("application/json", r.headers.get("content-type", ""))
            self.assertEqual(r.json(), {"detail": main.UNHANDLED_ERROR_DETAIL})
            self.assertEqual(r.headers.get("access-control-allow-origin"), NATIVE_ORIGIN)
            self.assertEqual(r.headers.get("access-control-allow-credentials"), "true")
            self.assertEqual(r.headers.get("x-content-type-options"), "nosniff")
            self.assertIn("content-security-policy", r.headers)
            self.assertNotIn("private detail", r.text)
            self.assertNotIn("Traceback", r.text)

    def test_unlisted_origin_still_gets_no_cors_grant(self):
        r = self.client.get("/__batch_f_probe/boom", headers={"Origin": "https://evil.example"})
        self.assertEqual(r.status_code, 500)
        self.assertNotIn("access-control-allow-origin", r.headers)

    def test_exception_still_propagates_to_server_logging(self):
        # The default TestClient re-raises what reached the server layer: the
        # error is still visible to logging/Sentry, not swallowed.
        with TestClient(main.app) as strict:
            with self.assertRaises(RuntimeError):
                strict.get("/__batch_f_probe/boom")

    def test_handled_http_errors_are_unchanged(self):
        r = self.client.get("/__batch_f_probe/refused", headers={"Origin": NATIVE_ORIGIN})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json(), {"detail": "Probe conflict."})
        self.assertEqual(r.headers.get("access-control-allow-origin"), NATIVE_ORIGIN)

    def test_middleware_is_innermost(self):
        # user_middleware is outermost-first; the error responder must sit
        # inside CORS and SecurityHeaders so its response passes through them.
        names = [m.cls.__name__ for m in main.app.user_middleware]
        self.assertEqual(names[-1], "UnhandledErrorResponseMiddleware", names)


class RateLimitAndPermissionMessageTests(unittest.TestCase):
    def test_obs8_provider_email_limit_says_when_to_retry(self):
        exc = main._verification_email_rate_limited("email rate limit exceeded, retry after 45 seconds")
        self.assertEqual(exc.status_code, 429)
        self.assertEqual(exc.headers, {"Retry-After": "45"})
        self.assertIn("45 seconds", exc.detail)
        exc = main._verification_email_rate_limited("For security purposes, you can only request this after 3 minutes")
        self.assertEqual(exc.headers, {"Retry-After": "180"})
        self.assertIn("3 minutes", exc.detail)
        exc = main._verification_email_rate_limited("rate limit")
        self.assertIn("a few minutes", exc.detail)
        self.assertNotIn("a little", exc.detail)
        with open(main.__file__, encoding="utf-8") as fh:
            self.assertNotIn("Please wait a little", fh.read())

    def test_obs8_account_lockout_names_the_wait(self):
        # check_rate_limit's lockout message already carries the seconds and
        # Retry-After; it is what sign-in and Business ID checks return.
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        self.assertIn('detail=f"Too many attempts. Please wait {seconds} seconds and try again."', source)

    def test_ux015_no_bare_access_denied_left(self):
        with open(main.__file__, encoding="utf-8") as fh:
            source = fh.read()
        self.assertNotIn('detail="Access denied', source)
        for expected in (
            "Only an Admin can review product deletion requests.",
            "Only an Admin or Manager can view account action requests.",
            "Only an Admin or Manager can view Business Day reopen requests.",
            "Only an Admin or Manager can change account permissions.",
        ):
            self.assertIn(expected, source)


if __name__ == "__main__":
    unittest.main()
