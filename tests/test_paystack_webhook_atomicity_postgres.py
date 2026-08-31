"""Opt-in PostgreSQL tests for F-03 webhook marker/effect atomicity."""
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
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine

ROOT = Path(__file__).resolve().parents[1]
SECRET = "test-paystack-webhook-secret"
APP_SECRET = "test-secret-0123456789abcdef0123456789abcdef0123456789abcdef"
ADMIN_URL = os.getenv("TEST_POSTGRES_ADMIN_URL", "").strip()


@unittest.skipUnless(ADMIN_URL, "TEST_POSTGRES_ADMIN_URL is not configured")
class PaystackWebhookAtomicityPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = f"cauldra_f03_{uuid.uuid4().hex[:12]}"
        if not re.fullmatch(r"cauldra_f03_[a-f0-9]{12}", cls.schema):
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
        if engine is not None and re.fullmatch(r"cauldra_f03_[a-f0-9]{12}", schema):
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

    def _billing_fixture(self, reference: str):
        db = self.main.SessionLocal()
        business = self.main.BusinessProfile(
            business_code=f"WH-{uuid.uuid4().hex[:8]}", company_name=f"Webhook {reference}",
            email="billing@webhook.example.com",
        )
        db.add(business)
        db.flush()
        sub = self.main.BusinessSubscription(
            business_id=business.id, plan="starter", billing_interval="monthly",
            status="pending_payment_method", current_period_start=datetime.utcnow(),
            current_period_end=datetime.utcnow() + timedelta(days=14),
        )
        db.add(sub)
        db.flush()
        db.add(self.main.PaymentRecord(
            business_id=business.id, subscription_id=sub.id, plan="starter",
            billing_interval="monthly", amount_kobo=500000, currency="NGN",
            paystack_reference=reference, status="initialized", purpose="subscription",
            transaction_metadata=json.dumps({
                "business_id": business.id, "plan": "starter", "billing_interval": "monthly",
                "purpose": "subscription", "customer_email": "billing@webhook.example.com",
            }),
        ))
        db.commit()
        result = (business.id, sub.id)
        db.close()
        return result

    @staticmethod
    def _event(reference: str) -> tuple[bytes, dict]:
        data = {
            "reference": reference, "status": "success", "amount": 500000,
            "currency": "NGN", "id": int(uuid.uuid4().int % 1_000_000_000),
            "customer": {"email": "billing@webhook.example.com"},
            "metadata": {"business_id": None, "plan": "starter", "billing_interval": "monthly", "purpose": "subscription"},
        }
        raw = json.dumps({"event": "charge.success", "data": data}, separators=(",", ":")).encode()
        return raw, data

    @staticmethod
    def _post(client, raw: bytes):
        signature = hmac.new(SECRET.encode(), raw, hashlib.sha512).hexdigest()
        return client.post(
            "/webhooks/paystack", content=raw,
            headers={"content-type": "application/json", "x-paystack-signature": signature},
        )

    def test_failed_processing_rolls_back_marker_and_effects_then_retry_succeeds_once(self):
        reference = f"f03-retry-{uuid.uuid4().hex}"
        business_id, sub_id = self._billing_fixture(reference)
        raw, verified = self._event(reference)
        verified["metadata"]["business_id"] = business_id
        raw = json.dumps({"event": "charge.success", "data": verified}, separators=(",", ":")).encode()
        from fastapi.testclient import TestClient

        client = TestClient(self.main.app, raise_server_exceptions=False)
        with patch.object(self.main, "paystack_verify_transaction", return_value=verified), patch.object(
            self.main, "add_audit", side_effect=RuntimeError("injected post-effect failure")
        ):
            failed = self._post(client, raw)
        self.assertEqual(failed.status_code, 500)

        db = self.main.SessionLocal()
        self.assertEqual(
            db.query(self.main.PaystackWebhookEvent).filter_by(event_key=f"charge.success:{reference}").count(),
            0,
        )
        self.assertEqual(db.query(self.main.PaymentRecord).filter_by(paystack_reference=reference).one().status, "initialized")
        self.assertEqual(db.query(self.main.BusinessSubscription).filter_by(id=sub_id).one().status, "pending_payment_method")
        db.close()

        with patch.object(self.main, "paystack_verify_transaction", return_value=verified):
            received = self._post(TestClient(self.main.app), raw)
            duplicate = self._post(TestClient(self.main.app), raw)
        self.assertEqual(received.status_code, 200, received.text)
        self.assertEqual(received.json()["status"], "received")
        self.assertEqual(duplicate.status_code, 200, duplicate.text)
        self.assertEqual(duplicate.json()["status"], "already_processed")

        db = self.main.SessionLocal()
        self.assertEqual(
            db.query(self.main.PaystackWebhookEvent).filter_by(event_key=f"charge.success:{reference}").count(),
            1,
        )
        self.assertEqual(db.query(self.main.PaymentRecord).filter_by(paystack_reference=reference).one().status, "success")
        self.assertEqual(db.query(self.main.BusinessSubscription).filter_by(id=sub_id).one().status, "active")
        self.assertEqual(db.query(self.main.AuditLog).filter_by(business_id=business_id, action="SUBSCRIPTION_ACTIVATED").count(), 1)
        db.close()

    def test_concurrent_duplicate_delivery_commits_one_marker_and_one_effect(self):
        reference = f"f03-race-{uuid.uuid4().hex}"
        business_id, _ = self._billing_fixture(reference)
        raw, verified = self._event(reference)
        verified["metadata"]["business_id"] = business_id
        raw = json.dumps({"event": "charge.success", "data": verified}, separators=(",", ":")).encode()
        from fastapi.testclient import TestClient

        def deliver_once():
            response = self._post(TestClient(self.main.app), raw)
            return response.status_code, response.json()["status"]

        with patch.object(self.main, "paystack_verify_transaction", return_value=verified):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _: deliver_once(), range(2)))
        self.assertEqual(sorted(status for _, status in results), ["already_processed", "received"])
        self.assertTrue(all(code == 200 for code, _ in results))

        db = self.main.SessionLocal()
        self.assertEqual(db.query(self.main.PaystackWebhookEvent).filter_by(event_key=f"charge.success:{reference}").count(), 1)
        self.assertEqual(db.query(self.main.AuditLog).filter_by(business_id=business_id, action="SUBSCRIPTION_ACTIVATED").count(), 1)
        db.close()


if __name__ == "__main__":
    unittest.main()
