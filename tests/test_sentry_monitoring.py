"""Backend Sentry integration regression checks.

Follows the existing pattern in tests/test_infrastructure.py: `import main` in
a fresh subprocess with a controlled environment (never the real .env), using
an intentionally unreachable DATABASE_URL + SUPPLY_AI_SKIP_DB_STARTUP_CHECK=
true so these tests exercise import-time/module-level logic only and never
touch a real PostgreSQL database. Nothing here needs TEST_POSTGRES_ADMIN_URL.
"""
from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SECRET = "test-secret-0123456789abcdef0123456789abcdef0123456789abcdef"
# Well-formed but non-existent Sentry DSN — never a real project, never
# reaches a real Sentry account, only exercises sentry_sdk's own parsing/init.
FAKE_DSN = "https://abc123def4567890abc123def4567890@o123456.ingest.us.sentry.io/6543210"


def _with_backend_on_path(env):
    backend = str(ROOT / "backend")
    existing = env.get("PYTHONPATH", "")
    return {**env, "PYTHONPATH": os.pathsep.join(p for p in (backend, existing) if p)}


def run(code, env):
    return subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=_with_backend_on_path(env), text=True, capture_output=True)


def base_env(**overrides):
    env = os.environ | {
        "DATABASE_URL": "postgresql+psycopg://baduser:badpass@127.0.0.1:5432/nonexistent",
        "SUPPLY_AI_SECRET_KEY": SECRET,
        "SUPPLY_AI_SKIP_DB_STARTUP_CHECK": "true",
    }
    env.update(overrides)
    env.pop("SENTRY_BACKEND_DSN", None) if "SENTRY_BACKEND_DSN" not in overrides else None
    env.pop("SENTRY_FRONTEND_DSN", None) if "SENTRY_FRONTEND_DSN" not in overrides else None
    return env


class SentryBackendStartupTests(unittest.TestCase):
    def test_imports_cleanly_with_no_sentry_dsn(self):
        env = base_env(SUPPLY_AI_ENV="development", SUPPLY_AI_TRUSTED_HOSTS="")
        result = run("import main; print('OK', main.SENTRY_BACKEND_DSN)", env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("OK ", result.stdout)

    def test_dsn_present_but_not_production_does_not_activate_sentry(self):
        """Sentry must stay off in ordinary local/staging development even if
        a DSN is accidentally present in the environment — local dev must
        never be noisy or send events."""
        env = base_env(SUPPLY_AI_ENV="development", SUPPLY_AI_TRUSTED_HOSTS="", SENTRY_BACKEND_DSN=FAKE_DSN)
        result = run("import main, sentry_sdk; print('ACTIVE', sentry_sdk.get_client().is_active())", env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ACTIVE False", result.stdout)

    def test_dsn_present_and_production_activates_sentry_with_safe_options(self):
        env = base_env(
            SUPPLY_AI_ENV="production", SUPPLY_AI_TRUSTED_HOSTS="example.test",
            SUPPLY_AI_REFRESH_COOKIE_SECURE="true", SENTRY_BACKEND_DSN=FAKE_DSN,
        )
        code = (
            "import main, sentry_sdk\n"
            "client = sentry_sdk.get_client()\n"
            "print('ACTIVE', client.is_active())\n"
            "print('TRACES', client.options.get('traces_sample_rate'))\n"
            "print('PROFILES', client.options.get('profiles_sample_rate'))\n"
            "print('PII', client.options.get('send_default_pii'))\n"
            "print('ENV', client.options.get('environment'))\n"
        )
        result = run(code, env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ACTIVE True", result.stdout)
        self.assertIn("TRACES 0", result.stdout)
        self.assertIn("PROFILES 0", result.stdout)
        self.assertIn("PII False", result.stdout)
        self.assertIn("ENV production", result.stdout)

    def test_missing_sentry_sdk_package_would_not_crash_startup(self):
        """The init call is wrapped in try/except — even if sentry_sdk failed
        to import or init for any reason, `import main` must still succeed."""
        env = base_env(SUPPLY_AI_ENV="production", SUPPLY_AI_TRUSTED_HOSTS="example.test",
                        SUPPLY_AI_REFRESH_COOKIE_SECURE="true", SENTRY_BACKEND_DSN="not-a-valid-dsn")
        result = run("import main; print('IMPORT_OK')", env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("IMPORT_OK", result.stdout)


class SentryScrubbingTests(unittest.TestCase):
    """Pure-logic checks for the before_send/before_breadcrumb redaction the
    task requires: Authorization/Cookie headers, tokens, passwords, OTP/MFA
    values, and provider secrets must never reach Sentry, however deeply
    nested, while unrelated fields must survive untouched."""

    def _env(self):
        return base_env(SUPPLY_AI_ENV="development", SUPPLY_AI_TRUSTED_HOSTS="")

    def test_headers_cookies_and_body_are_recursively_scrubbed(self):
        code = r'''
import main
event = {
    "request": {
        "headers": {"Authorization": "Bearer abc.def.ghi", "Cookie": "cauldra_refresh=xyz", "X-Api-Key": "sk_live_123", "Accept": "application/json"},
        "cookies": {"cauldra_refresh": "xyz"},
        "data": {"password": "hunter2", "otp": "123456", "mfa_secret": "JBSWY3DPEHPK3PXP", "nested": {"refresh_token": "rt_abc", "ok_field": "keep me"}},
        "query_string": "token=abc123&normal=1",
    },
    "extra": {"service_role_key": "srv_secret", "safe": "value"},
    "contexts": {"custom": {"client_secret": "cs_123", "keep": "yes"}},
    "exception": {"values": [{"stacktrace": {"frames": [{"vars": {"password": "leak-me", "x": 1}}]}}]},
}
out = main._sentry_before_send(event, {})
h = out["request"]["headers"]
assert h["Authorization"] == "[Filtered]"
assert h["Cookie"] == "[Filtered]"
assert h["X-Api-Key"] == "[Filtered]"
assert h["Accept"] == "application/json"
assert out["request"]["cookies"] == "[Filtered]"
d = out["request"]["data"]
assert d["password"] == "[Filtered]"
assert d["otp"] == "[Filtered]"
assert d["mfa_secret"] == "[Filtered]"
assert d["nested"]["refresh_token"] == "[Filtered]"
assert d["nested"]["ok_field"] == "keep me"
assert "normal=1" in out["request"]["query_string"]
assert "token=abc123" not in out["request"]["query_string"]
assert out["extra"]["service_role_key"] == "[Filtered]"
assert out["extra"]["safe"] == "value"
assert out["contexts"]["custom"]["client_secret"] == "[Filtered]"
assert out["contexts"]["custom"]["keep"] == "yes"
assert out["exception"]["values"][0]["stacktrace"]["frames"][0]["vars"]["password"] == "[Filtered]"
assert out["exception"]["values"][0]["stacktrace"]["frames"][0]["vars"]["x"] == 1
print("OK")
'''
        result = run(code, self._env())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("OK", result.stdout)

    def test_breadcrumb_data_is_scrubbed(self):
        code = r'''
import main
crumb = {"data": {"url": "https://api.example.com/x?api_key=abc", "headers": {"Authorization": "Bearer x"}}}
out = main._sentry_before_breadcrumb(crumb, {})
assert out["data"]["headers"]["Authorization"] == "[Filtered]"
print("OK")
'''
        result = run(code, self._env())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("OK", result.stdout)

    def test_scrubber_never_raises_on_malformed_event(self):
        """before_send/before_breadcrumb must never themselves crash error
        reporting — a malformed or unexpected event shape should pass
        through rather than raise."""
        code = r'''
import main
assert main._sentry_before_send({}, {}) == {}
assert main._sentry_before_send({"request": "not-a-dict"}, {}) == {"request": "not-a-dict"}
assert main._sentry_before_breadcrumb({}, {}) == {}
print("OK")
'''
        result = run(code, self._env())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("OK", result.stdout)


class PublicConfigEndpointTests(unittest.TestCase):
    """GET /config/public — no DB access needed, so a plain TestClient call
    against the imported app is safe here (no Postgres required)."""

    def test_frontend_dsn_withheld_outside_production(self):
        env = base_env(SUPPLY_AI_ENV="development", SUPPLY_AI_TRUSTED_HOSTS="",
                        SENTRY_FRONTEND_DSN="https://frontenddsn@o123456.ingest.us.sentry.io/111")
        code = '''
import main
from fastapi.testclient import TestClient
r = TestClient(main.app).get("/config/public")
body = r.json()
assert r.status_code == 200
assert body["sentry_frontend_dsn"] == "", body
assert body["environment"] == "development", body
print("OK")
'''
        result = run(code, env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("OK", result.stdout)

    def test_frontend_dsn_returned_in_production_never_leaks_backend_dsn(self):
        env = base_env(
            SUPPLY_AI_ENV="production", SUPPLY_AI_TRUSTED_HOSTS="example.test,testserver",
            SUPPLY_AI_REFRESH_COOKIE_SECURE="true",
            SENTRY_FRONTEND_DSN="https://frontenddsn@o123456.ingest.us.sentry.io/111",
            SENTRY_BACKEND_DSN=FAKE_DSN,
        )
        code = '''
import main
from fastapi.testclient import TestClient
r = TestClient(main.app).get("/config/public")
body = r.json()
assert body["sentry_frontend_dsn"] == "https://frontenddsn@o123456.ingest.us.sentry.io/111", body
assert body["environment"] == "production", body
assert "sentry_backend_dsn" not in body
assert main.SENTRY_BACKEND_DSN not in r.text
print("OK")
'''
        result = run(code, env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("OK", result.stdout)


if __name__ == "__main__":
    unittest.main()
