"""PostgreSQL verification for F-09 mutation idempotency consistency."""
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
from unittest.mock import patch

from sqlalchemy import create_engine

ROOT = Path(__file__).resolve().parents[1]
APP_SECRET = "test-secret-0123456789abcdef0123456789abcdef0123456789abcdef"
ADMIN_URL = os.getenv("TEST_POSTGRES_ADMIN_URL", "").strip()


@unittest.skipUnless(ADMIN_URL, "TEST_POSTGRES_ADMIN_URL is not configured")
class MutationIdempotencyPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = f"cauldra_f09_{uuid.uuid4().hex[:12]}"
        if not re.fullmatch(r"cauldra_f09_[a-f0-9]{12}", cls.schema):
            raise RuntimeError("Unsafe generated test schema name")
        cls.admin_engine = create_engine(ADMIN_URL, pool_pre_ping=True)
        with cls.admin_engine.begin() as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA "{cls.schema}"')
        cls.original_env = {key: os.environ.get(key) for key in (
            "DATABASE_URL", "PGOPTIONS", "SUPPLY_AI_ENV", "SUPPLY_AI_SECRET_KEY",
            "SUPPLY_AI_AUTO_CREATE_SCHEMA",
        )}
        os.environ.update({
            "DATABASE_URL": ADMIN_URL,
            "PGOPTIONS": f"-csearch_path={cls.schema}",
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
        if engine is not None and re.fullmatch(r"cauldra_f09_[a-f0-9]{12}", schema):
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

    def _tenant(self, label: str, with_product=False):
        db = self.main.SessionLocal()
        email = f"f09-{uuid.uuid4().hex[:8]}@example.com"
        business = self.main.BusinessProfile(
            business_code=f"F09-{uuid.uuid4().hex[:10]}", company_name=label,
            email=email, subscription_plan="starter", billing_interval="monthly",
        )
        db.add(business)
        db.flush()
        user = self.main.User(
            username=f"admin-{uuid.uuid4().hex[:10]}", password=self.main.hash_password("AdminPass9"),
            role="admin", firstname="F09", lastname="Admin", email=email, phone="08000000000",
            business_id=business.id,
        )
        db.add(user)
        db.flush()
        db.add(self.main.BusinessSubscription(
            business_id=business.id, plan="starter", billing_interval="monthly", status="active",
            payment_status="paid", current_period_start=datetime.utcnow() - timedelta(days=1),
            current_period_end=datetime.utcnow() + timedelta(days=29),
        ))
        db.add(self.main.Warehouse(business_id=business.id, name="Main Central Warehouse", is_active=True))
        product_id = None
        if with_product:
            product = self.main.Product(
                sku=f"SKU-{uuid.uuid4().hex[:10]}", name=f"{label} Product", category="Test",
                quantity=3, min_stock_level=0, cost_price=10.0, wholesale_price=15.0,
                retail_price=20.0, warehouse="Main Central Warehouse", initial_stock=3,
                business_id=business.id, owner_id=user.id,
            )
            db.add(product)
            db.flush()
            product_id = product.id
            db.add(self.main.WarehouseStock(
                business_id=business.id, product_id=product.id,
                warehouse="Main Central Warehouse", quantity=3,
            ))
        token = self.main.issue_token(user, db)
        result = business.id, product_id, token
        db.commit()
        db.close()
        return result

    @staticmethod
    def _headers(token):
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    @staticmethod
    def _product_payload(reference, name="Idempotent Product"):
        return {
            "name": name, "category": "Test", "sku": f"SKU-{reference[-12:]}",
            "quantity": 4, "min_stock_level": 1, "cost_price": 10.0,
            "wholesale_price": 15.0, "retail_price": 20.0,
            "warehouse": "Main Central Warehouse", "client_ref": reference,
        }

    def test_concurrent_product_create_applies_product_stock_audit_and_claim_once(self):
        from fastapi.testclient import TestClient

        business_id, _, token = self._tenant("F09 product create")
        reference = f"f09-product-{uuid.uuid4().hex}"
        payload = self._product_payload(reference)

        def submit(_):
            response = TestClient(self.main.app).post("/products/", json=payload, headers=self._headers(token))
            return response.status_code, response.json()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(submit, range(2)))
        self.assertTrue(all(status == 200 for status, _ in results))
        self.assertEqual(sorted(bool(body.get("duplicate")) for _, body in results), [False, True])
        self.assertEqual(results[0][1]["id"], results[1][1]["id"])
        reloaded = TestClient(self.main.app).get("/products/?limit=100&offset=0", headers=self._headers(token))
        self.assertEqual(reloaded.status_code, 200, reloaded.text)
        self.assertEqual(sum(1 for row in reloaded.json() if row["name"] == payload["name"]), 1)

        db = self.main.SessionLocal()
        products = db.query(self.main.Product).filter_by(business_id=business_id, client_ref=reference).all()
        self.assertEqual(len(products), 1)
        self.assertEqual(db.query(self.main.WarehouseStock).filter_by(business_id=business_id, product_id=products[0].id).count(), 1)
        self.assertEqual(db.query(self.main.AuditLog).filter_by(business_id=business_id, action="PRODUCT_CREATED").count(), 1)
        self.assertEqual(db.query(self.main.MutationIdempotency).filter_by(business_id=business_id, operation="product_create", client_ref=reference, status="completed").count(), 1)
        db.close()

    def test_concurrent_expense_create_applies_financial_and_day_effects_once(self):
        from fastapi.testclient import TestClient

        business_id, _, token = self._tenant("F09 expense")
        reference = f"f09-expense-{uuid.uuid4().hex}"
        payload = {"category": "Fuel", "amount": 1250.0, "note": "delivery", "client_ref": reference}

        def submit(_):
            response = TestClient(self.main.app).post("/expenses/", json=payload, headers=self._headers(token))
            return response.status_code, response.json()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(submit, range(2)))
        self.assertTrue(all(status == 200 for status, _ in results))
        self.assertEqual(sorted(bool(body.get("duplicate")) for _, body in results), [False, True])
        self.assertEqual(results[0][1]["id"], results[1][1]["id"])
        reloaded = TestClient(self.main.app).get("/expenses/?limit=100&offset=0", headers=self._headers(token))
        self.assertEqual(reloaded.status_code, 200, reloaded.text)
        self.assertEqual(reloaded.json()["total"], 1)
        self.assertEqual(reloaded.json()["expenses"][0]["amount"], 1250.0)

        db = self.main.SessionLocal()
        self.assertEqual(db.query(self.main.Expense).filter_by(business_id=business_id, client_ref=reference).count(), 1)
        self.assertEqual(db.query(self.main.BusinessDay).filter_by(business_id=business_id, is_open=True).count(), 1)
        self.assertEqual(db.query(self.main.AuditLog).filter_by(business_id=business_id, action="EXPENSE_RECORDED").count(), 1)
        self.assertEqual(db.query(self.main.MutationIdempotency).filter_by(business_id=business_id, operation="expense_create", client_ref=reference).count(), 1)
        db.close()

    def test_concurrent_product_update_applies_inventory_and_audit_once(self):
        from fastapi.testclient import TestClient

        business_id, product_id, token = self._tenant("F09 product update", with_product=True)
        reference = f"f09-update-{uuid.uuid4().hex}"
        payload = {"quantity": 5, "retail_price": 25.0, "client_ref": reference}

        def submit(_):
            response = TestClient(self.main.app).patch(f"/products/{product_id}", json=payload, headers=self._headers(token))
            return response.status_code, response.json()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(submit, range(2)))
        self.assertTrue(all(status == 200 for status, _ in results))
        self.assertEqual(sorted(bool(body.get("duplicate")) for _, body in results), [False, True])
        reloaded = TestClient(self.main.app).get("/products/?limit=100&offset=0", headers=self._headers(token))
        self.assertEqual(reloaded.status_code, 200, reloaded.text)
        self.assertEqual(next(row for row in reloaded.json() if row["id"] == product_id)["quantity"], 5)
        db = self.main.SessionLocal()
        self.assertEqual(db.query(self.main.Product).filter_by(id=product_id, business_id=business_id).one().quantity, 5)
        self.assertEqual(db.query(self.main.WarehouseStock).filter_by(product_id=product_id, business_id=business_id).one().quantity, 5)
        self.assertEqual(db.query(self.main.AuditLog).filter_by(business_id=business_id, action="PRODUCT_UPDATED").count(), 1)
        self.assertEqual(db.query(self.main.MutationIdempotency).filter_by(business_id=business_id, operation=f"product_update:{product_id}", client_ref=reference).count(), 1)
        db.close()

    def test_failure_rolls_back_claim_and_all_product_effects_then_retry_succeeds(self):
        from fastapi.testclient import TestClient

        business_id, _, token = self._tenant("F09 rollback")
        reference = f"f09-retry-{uuid.uuid4().hex}"
        payload = self._product_payload(reference, "Rollback Product")
        client = TestClient(self.main.app, raise_server_exceptions=False)
        with patch.object(self.main, "add_audit", side_effect=RuntimeError("injected audit failure")):
            failed = client.post("/products/", json=payload, headers=self._headers(token))
        self.assertEqual(failed.status_code, 500)
        db = self.main.SessionLocal()
        self.assertEqual(db.query(self.main.MutationIdempotency).filter_by(business_id=business_id, client_ref=reference).count(), 0)
        self.assertEqual(db.query(self.main.Product).filter_by(business_id=business_id, client_ref=reference).count(), 0)
        self.assertEqual(db.query(self.main.AuditLog).filter_by(business_id=business_id, action="PRODUCT_CREATED").count(), 0)
        db.close()

        retry = TestClient(self.main.app).post("/products/", json=payload, headers=self._headers(token))
        replay = TestClient(self.main.app).post("/products/", json=payload, headers=self._headers(token))
        self.assertEqual(retry.status_code, 200, retry.text)
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertTrue(replay.json()["duplicate"])

    def test_key_reuse_with_changed_payload_is_rejected_and_same_key_is_tenant_scoped(self):
        from fastapi.testclient import TestClient

        business_a, _, token_a = self._tenant("F09 tenant A")
        business_b, _, token_b = self._tenant("F09 tenant B")
        reference = f"f09-shared-{uuid.uuid4().hex}"
        first = TestClient(self.main.app).post("/products/", json=self._product_payload(reference, "Tenant A"), headers=self._headers(token_a))
        changed = TestClient(self.main.app).post("/products/", json=self._product_payload(reference, "Changed A"), headers=self._headers(token_a))
        other_tenant = TestClient(self.main.app).post("/products/", json=self._product_payload(reference, "Tenant B"), headers=self._headers(token_b))
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(changed.status_code, 409, changed.text)
        self.assertEqual(other_tenant.status_code, 200, other_tenant.text)
        db = self.main.SessionLocal()
        self.assertEqual(db.query(self.main.MutationIdempotency).filter_by(client_ref=reference).count(), 2)
        self.assertEqual(db.query(self.main.Product).filter(self.main.Product.business_id.in_([business_a, business_b]), self.main.Product.client_ref == reference).count(), 2)
        db.close()

    def test_validation_failure_leaves_no_claim_or_business_write(self):
        from fastapi.testclient import TestClient

        business_id, _, token = self._tenant("F09 validation")
        reference = f"f09-invalid-{uuid.uuid4().hex}"
        payload = self._product_payload(reference)
        payload["warehouse"] = "Missing Warehouse"
        response = TestClient(self.main.app).post("/products/", json=payload, headers=self._headers(token))
        self.assertEqual(response.status_code, 400, response.text)
        db = self.main.SessionLocal()
        self.assertEqual(db.query(self.main.MutationIdempotency).filter_by(business_id=business_id, client_ref=reference).count(), 0)
        self.assertEqual(db.query(self.main.Product).filter_by(business_id=business_id, client_ref=reference).count(), 0)
        db.close()


if __name__ == "__main__":
    unittest.main()
