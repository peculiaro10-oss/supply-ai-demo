"""PostgreSQL-backed verification for F-13 Paystack refund truthfulness."""
from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from unittest.mock import patch

from sqlalchemy import inspect

from tests.postgres_test_support import (
    ADMIN_URL,
    create_postgres_test_schema,
    drop_postgres_test_schema,
)


PREFIX = "cauldra_f13"
PAYSTACK_SECRET = "f13-test-paystack-secret"


@unittest.skipUnless(ADMIN_URL, "TEST_POSTGRES_ADMIN_URL is not configured")
class RefundStatePostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctx = create_postgres_test_schema(PREFIX)
        cls.main = cls.ctx.main
        cls.original_paystack_secret = cls.main.PAYSTACK_SECRET_KEY
        cls.main.PAYSTACK_SECRET_KEY = PAYSTACK_SECRET

    @classmethod
    def tearDownClass(cls):
        cls.main.PAYSTACK_SECRET_KEY = cls.original_paystack_secret
        drop_postgres_test_schema(cls.ctx, PREFIX)

    def _onboarding(self, reference: str, *, status: str = "initialized") -> int:
        db = self.main.SessionLocal()
        row = self.main.OnboardingAuthorization(
            paystack_reference=reference,
            email=f"{uuid.uuid4().hex[:8]}@example.com",
            plan="starter", billing_interval="monthly", amount_kobo=5000,
            status=status, expires_at=datetime.utcnow() + timedelta(hours=1),
        )
        db.add(row)
        db.commit()
        row_id = row.id
        db.close()
        return row_id

    def _payment(self, reference: str, label: str) -> tuple[int, int]:
        db = self.main.SessionLocal()
        business = self.main.BusinessProfile(
            business_code=f"F13-{uuid.uuid4().hex[:10]}", company_name=label,
            email=f"{uuid.uuid4().hex[:8]}@example.com",
        )
        db.add(business)
        db.flush()
        sub = self.main.BusinessSubscription(
            business_id=business.id, plan="starter", billing_interval="monthly",
            status="trialing", card_verified=True,
            current_period_start=datetime.utcnow(),
            current_period_end=datetime.utcnow() + timedelta(days=14),
        )
        db.add(sub)
        db.flush()
        record = self.main.PaymentRecord(
            business_id=business.id, subscription_id=sub.id, plan="starter",
            billing_interval="monthly", amount_kobo=5000, currency="NGN",
            paystack_reference=reference, status="success", purpose="card_verification",
            paystack_transaction_id=str(uuid.uuid4().int % 900_000_000 + 100_000_000),
        )
        db.add(record)
        db.commit()
        result = business.id, record.id
        db.close()
        return result

    def _trial_fixture(self, reference: str) -> tuple[int, int, str]:
        db = self.main.SessionLocal()
        email = f"{uuid.uuid4().hex[:8]}@example.com"
        business = self.main.BusinessProfile(
            business_code=f"F13-{uuid.uuid4().hex[:10]}", company_name="Trial journey",
            email=email, subscription_plan="starter", billing_interval="monthly",
        )
        db.add(business)
        db.flush()
        user = self.main.User(
            username=f"admin-{uuid.uuid4().hex[:10]}", password=self.main.hash_password("AdminPass9"),
            role="admin", firstname="F13", lastname="Admin", email=email,
            phone="08000000000", business_id=business.id,
        )
        db.add(user)
        db.flush()
        sub = self.main.BusinessSubscription(
            business_id=business.id, plan="starter", billing_interval="monthly",
            status="pending_payment_method", card_verified=False,
            current_period_start=datetime.utcnow(),
            current_period_end=datetime.utcnow() + timedelta(days=14),
        )
        db.add(sub)
        db.flush()
        record = self.main.PaymentRecord(
            business_id=business.id, subscription_id=sub.id, plan="starter",
            billing_interval="monthly", amount_kobo=5000, currency="NGN",
            paystack_reference=reference, status="initialized", purpose="card_verification",
            transaction_metadata=json.dumps({
                "business_id": business.id, "plan": "starter", "billing_interval": "monthly",
                "purpose": "trial_card_verification",
            }),
        )
        db.add(record)
        token = self.main.issue_token(user, db)
        db.commit()
        result = business.id, record.id, token
        db.close()
        return result

    @staticmethod
    def _verified(reference: str, tx_id: int) -> dict:
        return {
            "id": tx_id, "status": "success", "reference": reference,
            "amount": 5000, "currency": "NGN",
            "authorization": {
                "reusable": True, "channel": "card", "authorization_code": "AUTH_test",
                "last4": "4081", "card_type": "visa", "exp_month": "12", "exp_year": "2030",
            },
        }

    @staticmethod
    def _refund_payload(refund_id: int, status: str) -> dict:
        return {"status": True, "data": {"id": refund_id, "status": status, "amount": 5000, "currency": "NGN"}}

    @staticmethod
    def _post_refund_webhook(client, event_type: str, reference: str, refund_reference: str):
        raw = json.dumps({
            "event": event_type,
            "data": {
                "transaction_reference": reference,
                "refund_reference": refund_reference,
                "status": event_type.split(".", 1)[1],
                "amount": "5000", "currency": "NGN",
            },
        }, separators=(",", ":")).encode()
        signature = hmac.new(PAYSTACK_SECRET.encode(), raw, hashlib.sha512).hexdigest()
        return client.post(
            "/webhooks/paystack", content=raw,
            headers={"content-type": "application/json", "x-paystack-signature": signature},
        )

    def test_onboarding_pending_then_reconcile_processed_without_second_create(self):
        from fastapi.testclient import TestClient

        reference = f"cauldra_onboard_{uuid.uuid4().hex}"
        row_id = self._onboarding(reference)
        tx_id, refund_id = 701001, 801001
        calls = []

        def provider(method, path, body=None, timeout=15):
            calls.append((method, path))
            if method == "POST" and path == "/refund":
                return self._refund_payload(refund_id, "pending")
            if method == "GET" and path == f"/refund/{refund_id}":
                return self._refund_payload(refund_id, "processed")
            raise AssertionError((method, path, body))

        client = TestClient(self.main.app)
        with patch.object(self.main, "paystack_verify_transaction", return_value=self._verified(reference, tx_id)), \
                patch.object(self.main, "paystack_get_or_create_customer", return_value="CUS_test"), \
                patch.object(self.main, "paystack_request", side_effect=provider):
            first = client.post("/onboarding/payment/confirm", json={"reference": reference})
            replay = client.post("/onboarding/payment/confirm", json={"reference": reference})

        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json()["refund_status"], "pending")
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(replay.json()["refund_status"], "succeeded")
        self.assertEqual(sum(1 for method, path in calls if method == "POST" and path == "/refund"), 1)

        db = self.main.SessionLocal()
        row = db.query(self.main.OnboardingAuthorization).filter_by(id=row_id).one()
        self.assertEqual(row.refund_status, "succeeded")
        self.assertEqual(row.refund_provider_status, "processed")
        self.assertIsNotNone(row.refunded_at)
        self.assertEqual(row.refund_attempt_count, 1)
        db.close()

    def test_definitive_failure_is_truthful_and_retry_is_idempotent(self):
        from fastapi.testclient import TestClient

        reference = f"cauldra_onboard_{uuid.uuid4().hex}"
        row_id = self._onboarding(reference)
        tx_id, refund_id = 701002, 801002
        client = TestClient(self.main.app)

        with patch.object(self.main, "paystack_verify_transaction", return_value=self._verified(reference, tx_id)), \
                patch.object(self.main, "paystack_get_or_create_customer", return_value="CUS_test"), \
                patch.object(self.main, "paystack_request", side_effect=self.main.PaystackRequestError("rejected", definitive=True)):
            failed = client.post("/onboarding/payment/confirm", json={"reference": reference})
        self.assertEqual(failed.status_code, 200, failed.text)
        self.assertEqual(failed.json()["refund_status"], "failed")

        calls = []
        def retry_provider(method, path, body=None, timeout=15):
            calls.append((method, path))
            if method == "GET":
                return {"status": True, "data": []}
            return self._refund_payload(refund_id, "pending")

        with patch.object(self.main, "paystack_request", side_effect=retry_provider):
            retry = client.post("/onboarding/payment/confirm", json={"reference": reference})
            duplicate = client.post("/onboarding/payment/confirm", json={"reference": reference})
        self.assertEqual(retry.json()["refund_status"], "pending")
        self.assertEqual(duplicate.json()["refund_status"], "pending")
        self.assertEqual(sum(1 for method, path in calls if method == "POST" and path == "/refund"), 1)

        db = self.main.SessionLocal()
        row = db.query(self.main.OnboardingAuthorization).filter_by(id=row_id).one()
        self.assertIsNone(row.refunded_at)
        self.assertEqual(row.refund_attempt_count, 2)
        db.close()

    def test_ambiguous_create_outcome_stays_pending_and_never_blindly_reposts(self):
        reference = f"ambiguous-{uuid.uuid4().hex}"
        _, record_id = self._payment(reference, "Ambiguous tenant")
        db = self.main.SessionLocal()
        row = db.query(self.main.PaymentRecord).filter_by(id=record_id).one()
        tx_id = row.paystack_transaction_id
        post_calls = 0

        def ambiguous(method, path, body=None, timeout=15):
            nonlocal post_calls
            if method == "POST":
                post_calls += 1
                raise TimeoutError("response lost")
            return {"status": True, "data": []}

        with patch.object(self.main, "paystack_request", side_effect=ambiguous):
            first = self.main.ensure_verification_refund(db, row, reference, tx_id)
            db.refresh(row)
            second = self.main.ensure_verification_refund(db, row, reference, tx_id)
        self.assertEqual(first["refund_status"], "pending")
        self.assertEqual(second["refund_status"], "pending")
        self.assertEqual(post_calls, 1)
        self.assertIsNone(row.refunded_at)
        db.close()

    def test_concurrent_duplicate_initiation_creates_one_provider_refund(self):
        reference = f"concurrent-{uuid.uuid4().hex}"
        _, record_id = self._payment(reference, "Concurrent tenant")
        post_calls = 0
        lock = threading.Lock()

        def provider(method, path, body=None, timeout=15):
            nonlocal post_calls
            if method == "POST":
                with lock:
                    post_calls += 1
                time.sleep(0.2)
                return self._refund_payload(801004, "pending")
            return {"status": True, "data": []}

        def initiate_once(_):
            db = self.main.SessionLocal()
            row = db.query(self.main.PaymentRecord).filter_by(id=record_id).one()
            result = self.main.ensure_verification_refund(db, row, reference, row.paystack_transaction_id)
            db.close()
            return result["refund_status"]

        with patch.object(self.main, "paystack_request", side_effect=provider):
            with ThreadPoolExecutor(max_workers=2) as pool:
                states = list(pool.map(initiate_once, range(2)))
        self.assertEqual(states, ["pending", "pending"])
        self.assertEqual(post_calls, 1)

        db = self.main.SessionLocal()
        row = db.query(self.main.PaymentRecord).filter_by(id=record_id).one()
        self.assertEqual(row.refund_attempt_count, 1)
        self.assertIsNone(row.refunded_at)
        db.close()

    def test_trial_journey_reports_pending_then_webhook_succeeded_on_reload(self):
        from fastapi.testclient import TestClient

        reference = f"cauldra_trialcard_{uuid.uuid4().hex}"
        business_id, record_id, token = self._trial_fixture(reference)
        tx_id, refund_id = 701006, 801006
        client = TestClient(self.main.app)

        def provider(method, path, body=None, timeout=15):
            if method == "POST" and path == "/refund":
                return self._refund_payload(refund_id, "pending")
            if method == "GET" and path in {f"/refund/{refund_id}", "/refund/RFD-TRIAL"}:
                return self._refund_payload(refund_id, "processed")
            raise AssertionError((method, path, body))

        with patch.object(self.main, "paystack_verify_transaction", return_value=self._verified(reference, tx_id)), \
                patch.object(self.main, "paystack_get_or_create_customer", return_value="CUS_trial"), \
                patch.object(self.main, "paystack_create_subscription", return_value={"subscription_code": "SUB_trial"}), \
                patch.object(self.main, "paystack_request", side_effect=provider):
            started = client.post(
                "/subscription/trial/confirm", json={"reference": reference},
                headers={"Authorization": f"Bearer {token}"},
            )
        self.assertEqual(started.status_code, 200, started.text)
        self.assertEqual(started.json()["status"], "trialing")
        self.assertEqual(started.json()["refund_status"], "pending")

        db = self.main.SessionLocal()
        record = db.query(self.main.PaymentRecord).filter_by(id=record_id).one()
        self.assertIsNone(record.refunded_at)
        self.assertEqual(db.query(self.main.AuditLog).filter_by(business_id=business_id, action="TRIAL_VERIFICATION_REFUND_PENDING").count(), 1)
        self.assertEqual(db.query(self.main.AuditLog).filter_by(business_id=business_id, action="TRIAL_VERIFICATION_REFUND_SUCCEEDED").count(), 0)
        db.close()

        processed = self._post_refund_webhook(client, "refund.processed", reference, "RFD-TRIAL")
        self.assertEqual(processed.status_code, 200, processed.text)

        with patch.object(self.main, "paystack_request", side_effect=provider):
            reloaded = client.post(
                "/subscription/trial/confirm", json={"reference": reference},
                headers={"Authorization": f"Bearer {token}"},
            )
        self.assertEqual(reloaded.status_code, 200, reloaded.text)
        self.assertTrue(reloaded.json()["already_processed"])
        self.assertEqual(reloaded.json()["refund_status"], "succeeded")

        db = self.main.SessionLocal()
        record = db.query(self.main.PaymentRecord).filter_by(id=record_id).one()
        self.assertEqual(record.refund_status, "succeeded")
        self.assertIsNotNone(record.refunded_at)
        self.assertEqual(db.query(self.main.AuditLog).filter_by(business_id=business_id, action="TRIAL_VERIFICATION_REFUND_SUCCEEDED").count(), 1)
        db.close()

    def test_signed_webhook_rolls_back_then_retries_once_and_is_tenant_isolated(self):
        from fastapi.testclient import TestClient

        reference_a = f"webhook-a-{uuid.uuid4().hex}"
        reference_b = f"webhook-b-{uuid.uuid4().hex}"
        business_a, record_a = self._payment(reference_a, "Webhook tenant A")
        _, record_b = self._payment(reference_b, "Webhook tenant B")
        db = self.main.SessionLocal()
        for record_id in (record_a, record_b):
            row = db.query(self.main.PaymentRecord).filter_by(id=record_id).one()
            row.refund_status = "pending"
            row.refund_provider_status = "pending"
        db.commit()
        db.close()

        failing_client = TestClient(self.main.app, raise_server_exceptions=False)
        apply_state = self.main._apply_paystack_refund_state
        def fail_after_state_change(row, provider_refund, now=None):
            apply_state(row, provider_refund, now)
            raise RuntimeError("injected post-state failure")

        with patch.object(self.main, "_apply_paystack_refund_state", side_effect=fail_after_state_change):
            failed = self._post_refund_webhook(failing_client, "refund.processed", reference_a, "RFD-A")
        self.assertEqual(failed.status_code, 500)

        db = self.main.SessionLocal()
        self.assertEqual(db.query(self.main.PaymentRecord).filter_by(id=record_a).one().refund_status, "pending")
        self.assertIsNone(db.query(self.main.PaymentRecord).filter_by(id=record_a).one().refunded_at)
        self.assertEqual(db.query(self.main.PaystackWebhookEvent).filter_by(event_key=f"refund.processed:RFD-A").count(), 0)
        db.close()

        client = TestClient(self.main.app)
        received = self._post_refund_webhook(client, "refund.processed", reference_a, "RFD-A")
        duplicate = self._post_refund_webhook(client, "refund.processed", reference_a, "RFD-A")
        self.assertEqual(received.status_code, 200, received.text)
        self.assertEqual(received.json()["status"], "received")
        self.assertEqual(duplicate.json()["status"], "already_processed")

        db = self.main.SessionLocal()
        a = db.query(self.main.PaymentRecord).filter_by(id=record_a).one()
        b = db.query(self.main.PaymentRecord).filter_by(id=record_b).one()
        self.assertEqual(a.refund_status, "succeeded")
        self.assertIsNotNone(a.refunded_at)
        self.assertEqual(b.refund_status, "pending")
        self.assertIsNone(b.refunded_at)
        self.assertEqual(db.query(self.main.AuditLog).filter_by(business_id=business_a, action="TRIAL_VERIFICATION_REFUND_SUCCEEDED").count(), 1)
        self.assertEqual(db.query(self.main.PaystackWebhookEvent).filter_by(event_key="refund.processed:RFD-A").count(), 1)

        columns = {column["name"]: column for column in inspect(db.bind).get_columns("payment_records")}
        self.assertFalse(columns["refund_status"]["nullable"])
        self.assertTrue(columns["refund_provider_id"]["nullable"])
        db.close()


if __name__ == "__main__":
    unittest.main()
