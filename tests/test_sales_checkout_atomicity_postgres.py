"""Opt-in PostgreSQL concurrency and atomicity verification for F-02 checkout."""
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
class SalesCheckoutAtomicityPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = f"cauldra_f02_{uuid.uuid4().hex[:12]}"
        if not re.fullmatch(r"cauldra_f02_[a-f0-9]{12}", cls.schema):
            raise RuntimeError("Unsafe generated test schema name")
        cls.admin_engine = create_engine(ADMIN_URL, pool_pre_ping=True)
        with cls.admin_engine.begin() as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA "{cls.schema}"')
        cls.original_env = {key: os.environ.get(key) for key in (
            "DATABASE_URL", "PGOPTIONS", "SUPPLY_AI_ENV", "SUPPLY_AI_SECRET_KEY",
            "SUPPLY_AI_AUTO_CREATE_SCHEMA", "SUPPLY_AI_DB_SEARCH_PATH",
        )}
        os.environ.update({
            "DATABASE_URL": ADMIN_URL,
            "PGOPTIONS": f"-csearch_path={cls.schema}",
            "SUPPLY_AI_DB_SEARCH_PATH": cls.schema,
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
        if engine is not None and re.fullmatch(r"cauldra_f02_[a-f0-9]{12}", schema):
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

    def _tenant(self, label: str, quantities=(5,), active_day=False):
        db = self.main.SessionLocal()
        email = f"checkout-{uuid.uuid4().hex[:8]}@example.com"
        business = self.main.BusinessProfile(
            business_code=f"F02-{uuid.uuid4().hex[:10]}", company_name=label,
            email=email, subscription_plan="starter", billing_interval="monthly",
        )
        db.add(business)
        db.flush()
        user = self.main.User(
            username=f"cashier-{uuid.uuid4().hex[:10]}", password=self.main.hash_password("CashierPass9"),
            role="admin", firstname="F02", lastname="Cashier", email=email, phone="08000000000",
            business_id=business.id,
        )
        db.add(user)
        db.flush()
        db.add(self.main.BusinessSubscription(
            business_id=business.id, plan="starter", billing_interval="monthly", status="active",
            payment_status="paid", current_period_start=datetime.utcnow() - timedelta(days=1),
            current_period_end=datetime.utcnow() + timedelta(days=29),
        ))
        products = []
        for index, quantity in enumerate(quantities, start=1):
            product = self.main.Product(
                sku=f"SKU-{uuid.uuid4().hex[:10]}", name=f"{label} Product {index}", category="Test",
                quantity=quantity, min_stock_level=0, cost_price=10.0, wholesale_price=15.0,
                retail_price=20.0, warehouse="Main Central Warehouse", initial_stock=quantity,
                business_id=business.id, owner_id=user.id,
            )
            db.add(product)
            db.flush()
            db.add(self.main.WarehouseStock(
                business_id=business.id, product_id=product.id,
                warehouse="Main Central Warehouse", quantity=quantity,
            ))
            products.append(product.id)
        if active_day:
            db.add(self.main.BusinessDay(
                business_id=business.id, date=datetime.utcnow().date().isoformat(),
                is_open=True, status="OPEN", opened_by_id=user.id,
                opened_by_name=user.username, opened_by_role=user.role,
            ))
        token = self.main.issue_token(user, db)
        result = business.id, user.id, products, token
        db.commit()
        db.close()
        return result

    @staticmethod
    def _checkout(client, token: str, product_id: int, quantity: int, client_ref: str):
        return client.post(
            "/sales/checkout",
            json={"items": [{"product_id": product_id, "quantity": quantity, "unit_price": 20.0}], "client_ref": client_ref},
            headers={"Authorization": f"Bearer {token}"},
        )

    def test_complete_checkout_journey_commits_one_consistent_transaction(self):
        from fastapi.testclient import TestClient

        business_id, _, products, token = self._tenant("F02 journey", quantities=(5,))
        reference = f"f02-journey-{uuid.uuid4().hex}"
        client = TestClient(self.main.app)
        response = self._checkout(client, token, products[0], 2, reference)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["transaction_id"], reference)
        self.assertEqual(response.json()["updated_products"], [{"id": products[0], "quantity": 3}])

        detail = client.get(f"/sales/transactions/{reference}", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertEqual(sum(item["quantity"] for item in detail.json()["items"]), 2)

        db = self.main.SessionLocal()
        self.assertEqual(db.query(self.main.Product).filter_by(id=products[0], business_id=business_id).one().quantity, 3)
        self.assertEqual(db.query(self.main.WarehouseStock).filter_by(product_id=products[0], business_id=business_id).one().quantity, 3)
        self.assertEqual(db.query(self.main.SaleTransaction).filter_by(business_id=business_id, client_ref=reference).count(), 1)
        self.assertEqual(db.query(self.main.SaleModel).filter_by(business_id=business_id, client_ref=reference).count(), 1)
        self.assertEqual(db.query(self.main.AuditLog).filter_by(business_id=business_id, action="SALE_COMPLETED").count(), 1)
        self.assertEqual(db.query(self.main.BusinessDay).filter_by(business_id=business_id, is_open=True).count(), 1)
        db.close()

    def test_concurrent_different_checkouts_cannot_oversell_or_lose_stock_update(self):
        from fastapi.testclient import TestClient

        business_id, _, products, token = self._tenant("F02 competing", quantities=(1,), active_day=True)

        def submit(index):
            response = self._checkout(TestClient(self.main.app), token, products[0], 1, f"f02-compete-{index}-{uuid.uuid4().hex}")
            return response.status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(submit, range(2)))
        self.assertEqual(sorted(statuses), [200, 409])

        db = self.main.SessionLocal()
        self.assertEqual(db.query(self.main.Product).filter_by(id=products[0]).one().quantity, 0)
        self.assertEqual(db.query(self.main.WarehouseStock).filter_by(product_id=products[0], business_id=business_id).one().quantity, 0)
        self.assertEqual(db.query(self.main.SaleModel).filter_by(business_id=business_id).count(), 1)
        self.assertEqual(db.query(self.main.SaleTransaction).filter_by(business_id=business_id).count(), 1)
        self.assertEqual(db.query(self.main.AuditLog).filter_by(business_id=business_id, action="SALE_COMPLETED").count(), 1)
        db.close()

    def test_concurrent_same_client_reference_is_idempotent_after_last_unit_is_sold(self):
        from fastapi.testclient import TestClient

        business_id, _, products, token = self._tenant("F02 duplicate", quantities=(1,), active_day=True)
        reference = f"f02-duplicate-{uuid.uuid4().hex}"

        def submit(_):
            response = self._checkout(TestClient(self.main.app), token, products[0], 1, reference)
            return response.status_code, response.json()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(submit, range(2)))
        self.assertTrue(all(status == 200 for status, _ in results))
        self.assertEqual(sorted(bool(body.get("duplicate")) for _, body in results), [False, True])

        db = self.main.SessionLocal()
        self.assertEqual(db.query(self.main.Product).filter_by(id=products[0]).one().quantity, 0)
        self.assertEqual(db.query(self.main.SaleModel).filter_by(business_id=business_id, client_ref=reference).count(), 1)
        self.assertEqual(db.query(self.main.SaleTransaction).filter_by(business_id=business_id, client_ref=reference).count(), 1)
        self.assertEqual(db.query(self.main.AuditLog).filter_by(business_id=business_id, action="SALE_COMPLETED").count(), 1)
        db.close()

    def test_multiline_shortage_rolls_back_header_day_stock_sales_and_audits(self):
        from fastapi.testclient import TestClient

        business_id, _, products, token = self._tenant("F02 multiline rollback", quantities=(5, 1))
        reference = f"f02-short-{uuid.uuid4().hex}"
        response = TestClient(self.main.app).post(
            "/sales/checkout",
            json={"items": [
                {"product_id": products[0], "quantity": 2, "unit_price": 20.0},
                {"product_id": products[1], "quantity": 2, "unit_price": 20.0},
            ], "client_ref": reference},
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(response.status_code, 409, response.text)
        db = self.main.SessionLocal()
        self.assertEqual([db.query(self.main.Product).filter_by(id=pid).one().quantity for pid in products], [5, 1])
        self.assertEqual(db.query(self.main.SaleTransaction).filter_by(business_id=business_id, client_ref=reference).count(), 0)
        self.assertEqual(db.query(self.main.SaleModel).filter_by(business_id=business_id, client_ref=reference).count(), 0)
        self.assertEqual(db.query(self.main.BusinessDay).filter_by(business_id=business_id).count(), 0)
        self.assertEqual(db.query(self.main.AuditLog).filter_by(business_id=business_id).count(), 0)
        db.close()

    def test_post_stock_failure_rolls_back_all_checkout_writes(self):
        from fastapi.testclient import TestClient

        business_id, _, products, token = self._tenant("F02 injected rollback", quantities=(3,), active_day=True)
        reference = f"f02-failure-{uuid.uuid4().hex}"
        with patch.object(self.main, "add_audit", side_effect=RuntimeError("injected audit failure")):
            response = self._checkout(TestClient(self.main.app, raise_server_exceptions=False), token, products[0], 2, reference)
        self.assertEqual(response.status_code, 500)
        db = self.main.SessionLocal()
        self.assertEqual(db.query(self.main.Product).filter_by(id=products[0]).one().quantity, 3)
        self.assertEqual(db.query(self.main.WarehouseStock).filter_by(product_id=products[0], business_id=business_id).one().quantity, 3)
        self.assertEqual(db.query(self.main.SaleTransaction).filter_by(business_id=business_id, client_ref=reference).count(), 0)
        self.assertEqual(db.query(self.main.SaleModel).filter_by(business_id=business_id, client_ref=reference).count(), 0)
        self.assertEqual(db.query(self.main.AuditLog).filter_by(business_id=business_id, action="SALE_COMPLETED").count(), 0)
        db.close()

    def test_foreign_tenant_product_is_rejected_without_touching_either_tenant(self):
        from fastapi.testclient import TestClient

        business_a, _, products_a, token_a = self._tenant("F02 tenant A", quantities=(4,), active_day=True)
        business_b, _, products_b, _ = self._tenant("F02 tenant B", quantities=(7,), active_day=True)
        reference = f"f02-tenant-{uuid.uuid4().hex}"
        response = self._checkout(TestClient(self.main.app), token_a, products_b[0], 1, reference)
        self.assertEqual(response.status_code, 409, response.text)
        db = self.main.SessionLocal()
        self.assertEqual(db.query(self.main.Product).filter_by(id=products_a[0], business_id=business_a).one().quantity, 4)
        self.assertEqual(db.query(self.main.Product).filter_by(id=products_b[0], business_id=business_b).one().quantity, 7)
        self.assertEqual(db.query(self.main.SaleTransaction).filter_by(client_ref=reference).count(), 0)
        self.assertEqual(db.query(self.main.SaleModel).filter_by(client_ref=reference).count(), 0)
        db.close()


if __name__ == "__main__":
    unittest.main()
