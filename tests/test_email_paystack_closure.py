"""Executable email/Paystack closure tests with disposable local state.

Providers are mocked. These tests prove the exact challenge and initialization
state machines, not real Supabase delivery, Paystack checkout, or PostgreSQL.
"""
import os
os.environ["PYTHON_DOTENV_DISABLED"] = "1"
os.environ["SUPPLY_AI_SKIP_DB_STARTUP_CHECK"] = "true"
os.environ["DATABASE_URL"] = "postgresql+psycopg://test:test@127.0.0.1:65432/cauldra_test"
os.environ["SUPPLY_AI_SECRET_KEY"] = "isolated-test-secret-012345678901234567890123456789"

import secrets
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import main


class EmailPaystackClosureTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        main.Base.metadata.create_all(self.engine, tables=[
            main.OnboardingEmailChallenge.__table__,
            main.OnboardingAuthorization.__table__,
            main.AuthFailure.__table__,
        ])
        self.Session = sessionmaker(bind=self.engine)

        def db_override():
            with self.Session() as session:
                yield session

        main.app.dependency_overrides[main.get_db] = db_override
        self.config_patches = [
            patch.object(main, "SUPPLY_AI_FRONTEND_URL", "https://app.example.com"),
            patch.object(main, "SUPABASE_EMAIL_REDIRECT_URL", "https://app.example.com"),
            patch.object(main, "ONBOARDING_EMAIL_CALLBACK_BASE_URL", "https://api.example.com"),
            patch.object(main, "PAYSTACK_SECRET_KEY", "test-provider-secret"),
        ]
        for item in self.config_patches:
            item.start()
        self.client = TestClient(main.app)

    def tearDown(self):
        self.client.close()
        main.app.dependency_overrides.clear()
        for item in reversed(self.config_patches):
            item.stop()
        self.engine.dispose()

    def _challenge(self, *, platform="web", status="pending"):
        challenge_id = secrets.token_hex(32)
        with self.Session() as db:
            db.add(main.OnboardingEmailChallenge(
                challenge_id=challenge_id,
                email="owner@example.com",
                plan="starter",
                billing_interval="monthly",
                platform=platform,
                return_target="https://app.example.com/" if platform == "web" else "cauldra://auth/email-verified",
                status=status,
                verified_at=datetime.utcnow() if status == "verified" else None,
                created_at=datetime.utcnow(),
                expires_at=datetime.utcnow() + timedelta(minutes=20),
            ))
            db.commit()
        return challenge_id

    def test_configuration_roles_are_not_conflated(self):
        config = main.validate_email_payment_public_urls(required=True)
        self.assertEqual(config["frontend_origin"], "https://app.example.com")
        self.assertEqual(config["email_callback_origin"], "https://api.example.com")
        with patch.object(main, "SUPABASE_EMAIL_REDIRECT_URL", "https://api.example.com"):
            with self.assertRaisesRegex(RuntimeError, "frontend origin"):
                main.validate_email_payment_public_urls(required=True)
        with patch.object(main, "SUPPLY_AI_FRONTEND_URL", "javascript:alert(1)"):
            with self.assertRaisesRegex(RuntimeError, "public HTTPS origin"):
                main.validate_email_payment_public_urls(required=True)

    def test_callback_commit_then_exact_web_poll(self):
        challenge_id = self._challenge()
        proof = {"user": {"email": "owner@example.com", "email_confirmed_at": "2026-09-09T00:00:00Z"},
                 "access_token": "discarded", "refresh_token": "discarded"}
        with patch.object(main, "_onboarding_provider_post", return_value=proof):
            callback = self.client.post("/onboarding/email/verify/confirm", json={
                "challenge_id": challenge_id, "code": "pkce-code"
            })
        self.assertEqual(callback.status_code, 200, callback.text)
        self.assertEqual(callback.json()["status"], "verified")
        self.assertEqual(callback.json()["expected_return_origin"], "https://app.example.com")
        poll = self.client.post("/onboarding/email/verify/confirm", json={
            "challenge_id": challenge_id, "platform": "web"
        })
        self.assertEqual(poll.status_code, 200, poll.text)
        self.assertEqual(poll.json()["status"], "verified")
        mismatch = self.client.post("/onboarding/email/verify/confirm", json={
            "challenge_id": challenge_id, "platform": "native_android"
        })
        self.assertEqual(mismatch.status_code, 409)
        self.assertEqual(mismatch.json()["detail"]["reason"], "platform_mismatch")
        with self.Session() as db:
            self.assertEqual(db.get(main.OnboardingEmailChallenge, challenge_id).status, "verified")

    def test_pending_check_now_stays_pending(self):
        challenge_id = self._challenge()
        result = self.client.post("/onboarding/email/verify/confirm", json={
            "challenge_id": challenge_id, "platform": "web"
        })
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["status"], "pending")

    def test_definitive_init_rejection_allows_one_safe_new_reference(self):
        challenge_id = self._challenge(status="verified")
        rejected = main.PaystackRequestError("rejected", definitive=True, http_status=400)
        success = {"data": {
            "reference": "placeholder",
            "access_code": "access-code",
            "authorization_url": "https://checkout.paystack.com/secure/abc",
        }}

        def provider(_method, _path, body):
            if provider.calls == 0:
                provider.calls += 1
                raise rejected
            success["data"]["reference"] = body["reference"]
            return success
        provider.calls = 0

        body = {"challenge_id": challenge_id, "email": "owner@example.com",
                "plan": "starter", "billing_interval": "monthly"}
        with patch.object(main, "paystack_request", side_effect=provider):
            first = self.client.post("/onboarding/payment/init",
                headers={"Idempotency-Key": "firstattemptkey1234"}, json=body)
            self.assertEqual(first.status_code, 502, first.text)
            self.assertTrue(first.json()["detail"]["can_restart"])
            first_reference = first.json()["detail"]["reference"]
            second = self.client.post("/onboarding/payment/init",
                headers={"Idempotency-Key": "secondattemptkey123"}, json=body)
        self.assertEqual(second.status_code, 200, second.text)
        payload = second.json()
        self.assertNotEqual(payload["reference"], first_reference)
        self.assertEqual(payload["access_code"], "access-code")
        self.assertEqual(payload["authorization_url"], "https://checkout.paystack.com/secure/abc")
        for field in ("amount_kobo", "currency", "plan", "billing_interval", "callback_url"):
            self.assertIn(field, payload)
        with self.Session() as db:
            self.assertEqual(db.query(main.OnboardingAuthorization).count(), 1)
            self.assertEqual(db.get(main.OnboardingEmailChallenge, challenge_id).status, "consumed")


if __name__ == "__main__":
    unittest.main(verbosity=2)
