"""Opt-in PostgreSQL tests for F-05 registration atomicity.

Set TEST_POSTGRES_ADMIN_URL to a disposable-capable PostgreSQL connection. The
suite creates a unique schema, migrates it, runs only synthetic data, and drops
the schema in tearDownClass. It never writes to the application's public schema.
"""
from __future__ import annotations

import importlib
import os
import re
import subprocess
import sys
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine, event

ROOT = Path(__file__).resolve().parents[1]
SECRET = "test-secret-0123456789abcdef0123456789abcdef0123456789abcdef"
ADMIN_URL = os.getenv("TEST_POSTGRES_ADMIN_URL", "").strip()


@unittest.skipUnless(ADMIN_URL, "TEST_POSTGRES_ADMIN_URL is not configured")
class RegistrationAtomicityPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = f"cauldra_f05_{uuid.uuid4().hex[:12]}"
        if not re.fullmatch(r"cauldra_f05_[a-f0-9]{12}", cls.schema):
            raise RuntimeError("Unsafe generated test schema name")
        cls.admin_engine = create_engine(ADMIN_URL, pool_pre_ping=True)
        with cls.admin_engine.begin() as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA "{cls.schema}"')

        cls.scoped_url = ADMIN_URL
        cls.original_env = {key: os.environ.get(key) for key in (
            "DATABASE_URL", "PGOPTIONS", "SUPPLY_AI_ENV", "SUPPLY_AI_SECRET_KEY", "SUPPLY_AI_AUTO_CREATE_SCHEMA"
        )}
        os.environ.update({
            "DATABASE_URL": cls.scoped_url,
            "PGOPTIONS": f"-csearch_path={cls.schema}",
            "SUPPLY_AI_ENV": "development",
            "SUPPLY_AI_SECRET_KEY": SECRET,
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
        cls.original_plan_code = cls.main.PLAN_CONFIG["starter"].get("paystack_monthly_plan_code")
        cls.main.PLAN_CONFIG["starter"]["paystack_monthly_plan_code"] = None

    @classmethod
    def _drop_schema(cls):
        schema = getattr(cls, "schema", "")
        engine = getattr(cls, "admin_engine", None)
        if engine is not None and re.fullmatch(r"cauldra_f05_[a-f0-9]{12}", schema):
            with engine.begin() as connection:
                connection.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "main"):
            cls.main.PLAN_CONFIG["starter"]["paystack_monthly_plan_code"] = cls.original_plan_code
            cls.main.engine.dispose()
        cls._drop_schema()
        if hasattr(cls, "admin_engine"):
            cls.admin_engine.dispose()
        for key, value in getattr(cls, "original_env", {}).items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _authorization(self, reference: str, email: str):
        db = self.main.SessionLocal()
        db.add(self.main.OnboardingAuthorization(
            paystack_reference=reference,
            email=email,
            plan="starter",
            billing_interval="monthly",
            amount_kobo=5000,
            status="verified",
            paystack_customer_code=f"CUS_{reference}",
            paystack_authorization_code=f"AUTH_{reference}",
            card_last4="1234",
            card_type="visa",
            verified_at=datetime.utcnow(),
            expires_at=datetime.utcnow() + timedelta(hours=1),
        ))
        db.commit()
        db.close()

    @staticmethod
    def _payload(reference: str, suffix: str) -> dict:
        return {
            "company_name": f"Atomic Registration {suffix}",
            "email": f"business-{suffix}@atomic.example.com",
            "phone": "+2348012345678",
            "address": "1 Atomic Street",
            "firstname": "Atomic",
            "lastname": "Admin",
            "owner_email": f"owner-{suffix}@atomic.example.com",
            "owner_phone": "+2348012345678",
            "username": f"Atomic Admin {suffix}",
            "password": "AtomicAdmin9",
            "position": "Admin",
            "country": "Nigeria",
            "country_code": "NG",
            "language": "en",
            "payment_reference": reference,
        }

    def test_mid_registration_failure_rolls_back_authorization_and_tenant(self):
        reference = f"f05-rollback-{uuid.uuid4().hex}"
        suffix = uuid.uuid4().hex[:8]
        payload = self._payload(reference, suffix)
        self._authorization(reference, payload["owner_email"])

        def fail_subscription_insert(mapper, connection, target):
            raise RuntimeError("injected subscription insert failure")

        event.listen(self.main.BusinessSubscription, "before_insert", fail_subscription_insert)
        try:
            from fastapi.testclient import TestClient
            response = TestClient(self.main.app, raise_server_exceptions=False).post(
                "/auth/register-business", json=payload
            )
        finally:
            event.remove(self.main.BusinessSubscription, "before_insert", fail_subscription_insert)

        self.assertEqual(response.status_code, 500)
        db = self.main.SessionLocal()
        auth = db.query(self.main.OnboardingAuthorization).filter_by(paystack_reference=reference).one()
        self.assertEqual(auth.status, "verified")
        self.assertIsNone(auth.consumed_at)
        self.assertEqual(db.query(self.main.BusinessProfile).filter_by(company_name=payload["company_name"]).count(), 0)
        self.assertEqual(db.query(self.main.User).filter_by(email=payload["owner_email"]).count(), 0)
        db.close()

    def test_successful_registration_commits_complete_ecosystem_and_login_journey(self):
        reference = f"f05-success-{uuid.uuid4().hex}"
        suffix = uuid.uuid4().hex[:8]
        payload = self._payload(reference, suffix)
        self._authorization(reference, payload["owner_email"])

        from fastapi.testclient import TestClient
        registration_client = TestClient(self.main.app)
        registered = registration_client.post("/auth/register-business", json=payload)
        self.assertEqual(registered.status_code, 200, registered.text)
        body = registered.json()
        token = body["access_token"]

        me = registration_client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(me.status_code, 200, me.text)
        self.assertEqual(me.json()["role"], "admin")
        logout = registration_client.post("/auth/logout", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(logout.status_code, 200, logout.text)
        fresh_login = TestClient(self.main.app).post("/auth/admin-login", json={
            "business_id": body["business_code"],
            "username": payload["username"],
            "password": payload["password"],
        })
        self.assertEqual(fresh_login.status_code, 200, fresh_login.text)

        db = self.main.SessionLocal()
        auth = db.query(self.main.OnboardingAuthorization).filter_by(paystack_reference=reference).one()
        business = db.query(self.main.BusinessProfile).filter_by(company_name=payload["company_name"]).one()
        admin = db.query(self.main.User).filter_by(business_id=business.id, role="admin").one()
        self.assertEqual(auth.status, "consumed")
        self.assertIsNotNone(auth.consumed_at)
        self.assertEqual(db.query(self.main.Warehouse).filter_by(business_id=business.id).count(), 1)
        self.assertEqual(db.query(self.main.BusinessSubscription).filter_by(business_id=business.id).count(), 1)
        self.assertGreaterEqual(db.query(self.main.AuditLog).filter_by(business_id=business.id).count(), 2)
        self.assertGreaterEqual(db.query(self.main.RefreshSession).filter_by(user_id=admin.id).count(), 1)
        db.close()

    def test_concurrent_same_authorization_creates_exactly_one_business(self):
        reference = f"f05-race-{uuid.uuid4().hex}"
        suffix = uuid.uuid4().hex[:8]
        payload = self._payload(reference, suffix)
        self._authorization(reference, payload["owner_email"])

        from fastapi.testclient import TestClient

        def register_once():
            return TestClient(self.main.app).post("/auth/register-business", json=payload).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = sorted(pool.map(lambda _: register_once(), range(2)))
        self.assertEqual(statuses, [200, 409])

        db = self.main.SessionLocal()
        self.assertEqual(db.query(self.main.BusinessProfile).filter_by(company_name=payload["company_name"]).count(), 1)
        auth = db.query(self.main.OnboardingAuthorization).filter_by(paystack_reference=reference).one()
        self.assertEqual(auth.status, "consumed")
        db.close()


if __name__ == "__main__":
    unittest.main()
