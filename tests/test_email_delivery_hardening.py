"""AUTH-RECOVERY-001 regression tests — email sender validation (A2) and
diagnosable-but-safe delivery failures (A1).

The defect: RESEND_FROM was unset, so the code silently fell back to Resend's
shared testing sender (onboarding@resend.dev), which may only mail the Resend
account owner. Every other recipient was rejected with 403, and the app turned
that into a generic 502 with the provider's reason thrown away — so nobody
could tell a misconfigured sender from a provider outage.

Follows tests/test_sentry_monitoring.py: `import main` in a fresh subprocess
with a controlled environment and SUPPLY_AI_SKIP_DB_STARTUP_CHECK=true, so
these exercise module-level logic and the send path only. `requests.post` is
replaced inside the subprocess — nothing here contacts Resend or a database.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SECRET = "test-secret-0123456789abcdef0123456789abcdef0123456789abcdef"
FAKE_KEY = "re_FAKEKEYfortestsONLY0123456789"
RECIPIENT = "recipient.person@example.org"
CODE = "482915"

# Deployed environments (staging/production) refuse container-local upload
# storage at import (QA-STORAGE-001), so a deployed-style test environment
# carries a durable-storage configuration too. No network: the bucket check is
# skipped with SUPPLY_AI_SKIP_DB_STARTUP_CHECK, and the key is a fake.
DURABLE_STORAGE_TEST_ENV = {
    "SUPPLY_AI_STORAGE_BACKEND": "supabase",
    "SUPABASE_URL": "https://unit-test-project.supabase.co",
    "SUPABASE_SECRET_KEY": "sb_secret_UNITTESTONLY0123456789abcdefghijklmnop",
    "SUPABASE_STORAGE_BUCKET": "cauldra-private",
}


def base_env(**overrides):
    env = os.environ | DURABLE_STORAGE_TEST_ENV | {
        "DATABASE_URL": "postgresql+psycopg://baduser:badpass@127.0.0.1:5432/nonexistent",
        "SUPPLY_AI_SECRET_KEY": SECRET,
        "SUPPLY_AI_SKIP_DB_STARTUP_CHECK": "true",
        "SUPPLY_AI_TRUSTED_HOSTS": "",
        "RESEND_API_KEY": FAKE_KEY,
    }
    for key in ("RESEND_FROM", "SENTRY_BACKEND_DSN", "SENTRY_FRONTEND_DSN"):
        env.pop(key, None)
    env.update(overrides)
    backend = str(ROOT / "backend")
    env["PYTHONPATH"] = os.pathsep.join(p for p in (backend, env.get("PYTHONPATH", "")) if p)
    return env


def production_env(**overrides):
    # Everything else production insists on at import, so the only variable
    # under test is RESEND_FROM.
    return base_env(
        SUPPLY_AI_ENV="production",
        SUPPLY_AI_TRUSTED_HOSTS="app.example.test",
        SUPPLY_AI_REFRESH_COOKIE_SECURE="true",
        SUPPLY_AI_CORS_ORIGINS="https://app.example.test",
        SUPPLY_AI_FRONTEND_URL="https://app.example.test",
        ONBOARDING_EMAIL_CALLBACK_BASE_URL="https://api.example.test",
        **overrides,
    )


def run(code, env):
    return subprocess.run([sys.executable, "-c", textwrap.dedent(code)], cwd=ROOT, env=env,
                          text=True, capture_output=True)


# Replaces requests.post with a canned provider response, then calls the real
# send_recovery_email() and reports how it failed.
SEND_HARNESS = """
import json, requests, main
from fastapi import HTTPException

scenario = json.loads({scenario!r})

class FakeResponse:
    def __init__(self, status, body, headers):
        self.status_code = status
        self.ok = 200 <= status < 300
        self._body = body
        self.text = json.dumps(body) if body is not None else ""
        self.headers = headers
    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body

def fake_post(url, headers=None, json=None, timeout=None):
    if scenario.get("raise"):
        raise getattr(requests.exceptions, scenario["raise"])("simulated")
    return FakeResponse(scenario["status"], scenario.get("body"), scenario.get("headers", {{}}))

requests.post = fake_post
try:
    main.send_recovery_email({recipient!r}, "audit_admin", {code!r})
    print("RESULT ok")
except HTTPException as exc:
    print("RESULT", exc.status_code, exc.detail)
"""


def send(scenario, env=None):
    code = SEND_HARNESS.format(scenario=json.dumps(scenario), recipient=RECIPIENT, code=CODE)
    return run(code, env or base_env(SUPPLY_AI_ENV="staging", RESEND_FROM="Cauldra <no-reply@notify.example.test>"))


def delivery_records(stdout):
    return [json.loads(line.split("[email-delivery] ", 1)[1])
            for line in stdout.splitlines() if line.startswith("[email-delivery] ")]


class ResendSenderValidationTests(unittest.TestCase):
    """A2 — outside development there is no silent fallback to the shared
    testing sender; RESEND_FROM must be explicit."""

    def sender(self, env):
        return run("import main; print('SENDER', repr(main.RESEND_FROM)); print('ERR', repr(main.RESEND_SENDER_CONFIG_ERROR))", env)

    def test_development_keeps_the_local_fallback(self):
        result = self.sender(base_env(SUPPLY_AI_ENV="development"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("SENDER 'onboarding@resend.dev'", result.stdout)
        self.assertIn("ERR ''", result.stdout)

    def test_staging_with_explicit_sender_uses_it(self):
        result = self.sender(base_env(SUPPLY_AI_ENV="staging", RESEND_FROM="Cauldra <no-reply@notify.example.test>"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("SENDER 'Cauldra <no-reply@notify.example.test>'", result.stdout)

    def test_staging_without_sender_does_not_fall_back(self):
        result = self.sender(base_env(SUPPLY_AI_ENV="staging"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("SENDER ''", result.stdout)
        self.assertIn("ERR 'RESEND_FROM'", result.stdout)
        self.assertIn("[startup] EMAIL_SENDER_CONFIG_INVALID: RESEND_FROM", result.stdout)
        self.assertNotIn("onboarding@resend.dev", result.stdout)

    def test_staging_rejects_the_shared_testing_sender_explicitly_configured(self):
        result = self.sender(base_env(SUPPLY_AI_ENV="staging", RESEND_FROM="Cauldra <onboarding@resend.dev>"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ERR 'RESEND_FROM'", result.stdout)

    def test_staging_rejects_a_malformed_sender(self):
        result = self.sender(base_env(SUPPLY_AI_ENV="staging", RESEND_FROM="not an address"))
        self.assertIn("ERR 'RESEND_FROM'", result.stdout)

    def test_production_without_sender_fails_closed_naming_only_the_variable(self):
        result = self.sender(production_env())
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("RESEND_FROM", result.stderr)
        self.assertNotIn(FAKE_KEY, result.stderr + result.stdout)

    def test_production_rejects_the_shared_testing_sender(self):
        result = self.sender(production_env(RESEND_FROM="onboarding@resend.dev"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("RESEND_FROM", result.stderr)

    def test_production_with_a_valid_sender_imports(self):
        result = self.sender(production_env(RESEND_FROM="Cauldra <no-reply@notify.example.test>"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ERR ''", result.stdout)

    def test_unconfigured_sender_refuses_the_send_instead_of_using_the_tester(self):
        result = send({"status": 200, "body": {"id": "x"}}, env=base_env(SUPPLY_AI_ENV="staging"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("RESULT 503", result.stdout)
        records = delivery_records(result.stdout)
        self.assertEqual(records[-1]["category"], "configuration_error")
        self.assertEqual(records[-1]["setting"], "RESEND_FROM")


class DeliveryFailureClassificationTests(unittest.TestCase):
    """A1 — the user sees one safe, generic message; the server log says
    exactly which kind of failure it was."""

    def assert_generic_502(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("RESULT 502 We could not send the recovery email right now.", result.stdout)

    def assert_nothing_sensitive(self, result):
        out = result.stdout + result.stderr
        for secret in (FAKE_KEY, CODE, RECIPIENT, "Bearer "):
            self.assertNotIn(secret, out, f"{secret!r} leaked into output")

    def test_success_logs_nothing(self):
        result = send({"status": 200, "body": {"id": "abc"}})
        self.assertIn("RESULT ok", result.stdout)
        self.assertEqual(delivery_records(result.stdout), [])

    def test_the_original_defect_is_classified_as_configuration(self):
        body = {"statusCode": 403, "name": "validation_error",
                "message": "You can only send testing emails to your own email address (owner@example.net)."}
        result = send({"status": 403, "body": body, "headers": {"x-request-id": "req_123"}})
        self.assert_generic_502(result)
        record = delivery_records(result.stdout)[-1]
        self.assertEqual(record["category"], "configuration_error")
        self.assertEqual(record["provider"], "resend")
        self.assertEqual(record["purpose"], "password_recovery")
        self.assertEqual(record["http_status"], 403)
        self.assertEqual(record["provider_error"], "validation_error")
        self.assertEqual(record["request_id"], "req_123")
        # the provider message is kept, but any address inside it is masked
        self.assertIn("only send testing emails", record["provider_message"])
        self.assertNotIn("owner@example.net", record["provider_message"])
        self.assert_nothing_sensitive(result)

    def test_invalid_key_is_configuration(self):
        result = send({"status": 401, "body": {"name": "invalid_api_key", "message": "API key is invalid"}})
        self.assert_generic_502(result)
        self.assertEqual(delivery_records(result.stdout)[-1]["category"], "configuration_error")

    def test_invalid_from_address_is_configuration_even_as_422(self):
        result = send({"status": 422, "body": {"name": "invalid_from_address", "message": "Invalid `from` field."}})
        self.assert_generic_502(result)
        self.assertEqual(delivery_records(result.stdout)[-1]["category"], "configuration_error")

    def test_recipient_validation_is_a_provider_rejection(self):
        result = send({"status": 422, "body": {"name": "validation_error", "message": "Invalid `to` field."}})
        self.assert_generic_502(result)
        self.assertEqual(delivery_records(result.stdout)[-1]["category"], "provider_rejection")

    def test_rate_limit_is_a_provider_rejection(self):
        result = send({"status": 429, "body": {"name": "rate_limit_exceeded", "message": "Too many requests."}})
        self.assertEqual(delivery_records(result.stdout)[-1]["category"], "provider_rejection")

    def test_server_error_is_a_provider_outage(self):
        result = send({"status": 503, "body": None})
        self.assert_generic_502(result)
        record = delivery_records(result.stdout)[-1]
        self.assertEqual(record["category"], "provider_outage")
        self.assertIsNone(record["provider_error"])

    def test_timeout_is_a_provider_outage(self):
        result = send({"raise": "Timeout"})
        self.assert_generic_502(result)
        record = delivery_records(result.stdout)[-1]
        self.assertEqual(record["category"], "provider_outage")
        self.assertEqual(record["exception_type"], "Timeout")
        self.assert_nothing_sensitive(result)

    def test_connection_error_is_a_provider_outage(self):
        result = send({"raise": "ConnectionError"})
        self.assertEqual(delivery_records(result.stdout)[-1]["category"], "provider_outage")

    def test_unexpected_status_is_other_delivery_failure(self):
        result = send({"status": 302, "body": None})
        self.assert_generic_502(result)
        self.assertEqual(delivery_records(result.stdout)[-1]["category"], "other_delivery_failure")

    def test_missing_api_key_stays_a_503_and_is_logged_as_configuration(self):
        env = base_env(SUPPLY_AI_ENV="staging", RESEND_FROM="Cauldra <no-reply@notify.example.test>")
        env.pop("RESEND_API_KEY")
        result = send({"status": 200, "body": {"id": "x"}}, env=env)
        self.assertIn("RESULT 503", result.stdout)
        record = delivery_records(result.stdout)[-1]
        self.assertEqual(record["category"], "configuration_error")
        self.assertEqual(record["setting"], "RESEND_API_KEY")

    def test_provider_message_cannot_smuggle_a_key_or_code_into_the_log(self):
        body = {"name": "validation_error", "message": f"bad key {FAKE_KEY} for code {CODE} to {RECIPIENT}"}
        result = send({"status": 422, "body": body})
        self.assert_nothing_sensitive(result)


if __name__ == "__main__":
    unittest.main()
