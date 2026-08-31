"""Opt-in PostgreSQL verification for F-04 authoritative subscription activation."""
from __future__ import annotations

import hashlib
import hmac
import importlib
import json
import os
import re
import subprocess
import sys
import unittest
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from sqlalchemy import create_engine

ROOT = Path(__file__).resolve().parents[1]
SECRET = "test-paystack-verification-secret"
APP_SECRET = "test-secret-0123456789abcdef0123456789abcdef0123456789abcdef"
ADMIN_URL = os.getenv("TEST_POSTGRES_ADMIN_URL", "").strip()


@unittest.skipUnless(ADMIN_URL, "TEST_POSTGRES_ADMIN_URL is not configured")
class PaystackVerificationPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = f"cauldra_f04_{uuid.uuid4().hex[:12]}"
        if not re.fullmatch(r"cauldra_f04_[a-f0-9]{12}", cls.schema):
            raise RuntimeError("Unsafe generated test schema name")
        cls.admin_engine = create_engine(ADMIN_URL, pool_pre_ping=True)
        with cls.admin_engine.begin() as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA "{cls.schema}"')
        cls.original_env = {key: os.environ.get(key) for key in (
            "DATABASE_URL", "PGOPTIONS", "PAYSTACK_SECRET_KEY", "SUPPLY_AI_ENV",
            "SUPPLY_AI_SECRET_KEY", "SUPPLY_AI_AUTO_CREATE_SCHEMA", "SUPPLY_AI_DB_SEARCH_PATH",
        )}
        os.environ.update({
            "DATABASE_URL": ADMIN_URL,
            "PGOPTIONS": f"-csearch_path={cls.schema}",
            "SUPPLY_AI_DB_SEARCH_PATH": cls.schema,
            "PAYSTACK_SECRET_KEY": SECRET,
            "SUPPLY_AI_ENV": "development",
            "SUPPLY_AI_SECRET_KEY": APP_SECRET,
            "SUPPLY_AI_AUTO_CREATE_SCHEMA": "false",
        })
        migration = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT,
            env=os.environ.copy(), text=True, capture_output=True, check=False,
        )
        if migration.returncode != 0:
            cls._drop_schema()
            raise RuntimeError("PostgreSQL test schema migration failed; captured output suppressed to protect credentials")
        cls.main = importlib.import_module("main")

    @classmethod
    def _drop_schema(cls):
        schema = getattr(cls, "schema", "")
        engine = getattr(cls, "admin_engine", None)
        if engine is not None and re.fullmatch(r"cauldra_f04_[a-f0-9]{12}", schema):
            with engine.begin() as connection:
                connection.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "main"):
            cls.main.engine.dispose()
        cls._drop_schema()
        if hasattr(cls, "admin_engine"):
            cls.admin_engine.dispose()
        for key, value in getattr(cls, "original_env", {}).items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    @staticmethod
    def _post_webhook(client, data: dict):
        raw = json.dumps({"event": "charge.success", "data": data}, separators=(",", ":")).encode()
        signature = hmac.new(SECRET.encode(), raw, hashlib.sha512).hexdigest()
        return client.post(
            "/webhooks/paystack", content=raw,
            headers={"content-type": "application/json", "x-paystack-signature": signature},
        )

    def _tenant(self, label: str):
        db = self.main.SessionLocal()
        email = f"billing-{uuid.uuid4().hex[:8]}@example.com"
        business = self.main.BusinessProfile(
            business_code=f"F04-{uuid.uuid4().hex[:10]}", company_name=label,
            email=email, subscription_plan="starter", billing_interval="monthly",
        )
        db.add(business)
        db.flush()
        user = self.main.User(
            username=f"admin-{uuid.uuid4().hex[:10]}", password=self.main.hash_password("AdminPass9"),
            role="admin", firstname="F04", lastname="Admin", email=email, phone="08000000000",
            business_id=business.id,
        )
        db.add(user)
        db.flush()
        token = self.main.issue_token(user, db)
        result = business.id, user.id, email, token
        db.commit()
        db.close()
        return result

    def _direct_payment(self, business_id: int, email: str, reference: str):
        db = self.main.SessionLocal()
        sub = self.main.BusinessSubscription(
            business_id=business_id, plan="starter", billing_interval="monthly",
            status="pending_payment_method", current_period_start=datetime.utcnow(),
            current_period_end=datetime.utcnow() + timedelta(days=14),
        )
        db.add(sub)
        db.flush()
        db.add(self.main.PaymentRecord(
            business_id=business_id, subscription_id=sub.id, plan="starter",
            billing_interval="monthly", amount_kobo=500000, currency="NGN",
            paystack_reference=reference, status="initialized", purpose="subscription",
            transaction_metadata=json.dumps({
                "business_id": business_id, "plan": "starter", "billing_interval": "monthly",
                "purpose": "subscription", "customer_email": email,
            }),
        ))
        sub_id = sub.id
        db.commit()
        db.close()
        return sub_id

    @staticmethod
    def _verified(reference: str, business_id: int, email: str):
        return {
            "id": int(uuid.uuid4().int % 1_000_000_000), "status": "success",
            "reference": reference, "amount": 500000, "currency": "NGN",
            "customer": {"email": email},
            "metadata": {
                "business_id": business_id, "plan": "starter", "billing_interval": "monthly",
                "purpose": "subscription", "customer_email": email,
            },
        }

    def test_checkout_to_webhook_to_subscription_summary_uses_authoritative_verification(self):
        from fastapi.testclient import TestClient

        business_id, _, email, token = self._tenant("F04 complete journey")
        fake_response = Mock()
        fake_response.ok = True
        fake_response.json.return_value = {"status": True, "data": {"authorization_url": "https://paystack.test/authorize"}}
        client = TestClient(self.main.app)
        with patch("requests.post", return_value=fake_response):
            checkout = client.post(
                "/subscription/checkout", json={"plan": "starter", "billing_interval": "monthly"},
                headers={"Authorization": f"Bearer {token}"},
            )
        self.assertEqual(checkout.status_code, 200, checkout.text)
        reference = checkout.json()["reference"]
        verified = self._verified(reference, business_id, email)
        verified["amount"] = checkout.json()["amount_kobo"]
        with patch.object(self.main, "paystack_verify_transaction", return_value=verified) as verify:
            received = self._post_webhook(client, verified)
        self.assertEqual(received.status_code, 200, received.text)
        self.assertEqual(received.json()["status"], "received")
        verify.assert_called_once_with(reference)

        summary = client.get("/subscription/usage", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(summary.status_code, 200, summary.text)
        self.assertEqual(summary.json()["status"], "active")
        self.assertEqual(summary.json()["payment_status"], "paid")

        db = self.main.SessionLocal()
        record = db.query(self.main.PaymentRecord).filter_by(paystack_reference=reference).one()
        self.assertEqual(record.status, "success")
        self.assertIsNotNone(record.paid_at)
        stored = json.loads(record.transaction_metadata)
        self.assertEqual(stored["verification_source"], "paystack_verify_transaction")
        self.assertEqual(stored["paystack_transaction_id"], str(verified["id"]))
        self.assertEqual(db.query(self.main.AuditLog).filter_by(business_id=business_id, action="SUBSCRIPTION_ACTIVATED").count(), 1)
        db.close()

    def test_authoritative_mismatch_is_flagged_without_partial_write_or_cross_tenant_effect(self):
        from fastapi.testclient import TestClient

        business_a, _, email_a, _ = self._tenant("F04 tenant A")
        business_b, _, email_b, _ = self._tenant("F04 tenant B")
        reference = f"f04-mismatch-{uuid.uuid4().hex}"
        sub_a = self._direct_payment(business_a, email_a, reference)
        verified = self._verified(reference, business_b, email_b)
        verified["amount"] = 1
        verified["currency"] = "USD"

        with patch.object(self.main, "paystack_verify_transaction", return_value=verified):
            response = self._post_webhook(TestClient(self.main.app), verified)
        self.assertEqual(response.status_code, 200, response.text)

        db = self.main.SessionLocal()
        record = db.query(self.main.PaymentRecord).filter_by(paystack_reference=reference).one()
        self.assertEqual(record.status, "flagged_verification_mismatch")
        self.assertIsNone(record.paid_at)
        self.assertEqual(db.query(self.main.BusinessSubscription).filter_by(id=sub_a).one().status, "pending_payment_method")
        self.assertEqual(db.query(self.main.BusinessSubscription).filter_by(business_id=business_b).count(), 0)
        self.assertEqual(db.query(self.main.BusinessProfile).filter_by(id=business_a).one().subscription_plan, "starter")
        self.assertEqual(db.query(self.main.BusinessProfile).filter_by(id=business_b).one().subscription_plan, "starter")
        self.assertEqual(db.query(self.main.AuditLog).filter_by(business_id=business_a, action="SUBSCRIPTION_PAYMENT_VERIFICATION_MISMATCH").count(), 1)
        self.assertEqual(db.query(self.main.AuditLog).filter_by(business_id=business_b, action="SUBSCRIPTION_PAYMENT_VERIFICATION_MISMATCH").count(), 0)
        db.close()

    def test_provider_failure_rolls_back_marker_and_all_effects_then_retry_activates_once(self):
        from fastapi.testclient import TestClient

        business_id, _, email, _ = self._tenant("F04 retry")
        reference = f"f04-retry-{uuid.uuid4().hex}"
        sub_id = self._direct_payment(business_id, email, reference)
        verified = self._verified(reference, business_id, email)
        client = TestClient(self.main.app, raise_server_exceptions=False)

        with patch.object(self.main, "paystack_verify_transaction", side_effect=RuntimeError("provider unavailable")):
            failed = self._post_webhook(client, verified)
        self.assertEqual(failed.status_code, 502)

        db = self.main.SessionLocal()
        self.assertEqual(db.query(self.main.PaystackWebhookEvent).filter_by(event_key=f"charge.success:{reference}").count(), 0)
        self.assertEqual(db.query(self.main.PaymentRecord).filter_by(paystack_reference=reference).one().status, "initialized")
        self.assertEqual(db.query(self.main.BusinessSubscription).filter_by(id=sub_id).one().status, "pending_payment_method")
        self.assertEqual(db.query(self.main.AuditLog).filter_by(business_id=business_id, action="SUBSCRIPTION_ACTIVATED").count(), 0)
        db.close()

        with patch.object(self.main, "paystack_verify_transaction", return_value=verified):
            received = self._post_webhook(TestClient(self.main.app), verified)
            duplicate = self._post_webhook(TestClient(self.main.app), verified)
        self.assertEqual(received.json()["status"], "received")
        self.assertEqual(duplicate.json()["status"], "already_processed")
        db = self.main.SessionLocal()
        self.assertEqual(db.query(self.main.PaystackWebhookEvent).filter_by(event_key=f"charge.success:{reference}").count(), 1)
        self.assertEqual(db.query(self.main.AuditLog).filter_by(business_id=business_id, action="SUBSCRIPTION_ACTIVATED").count(), 1)
        db.close()


if __name__ == "__main__":
    unittest.main()
