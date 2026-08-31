"""PostgreSQL-backed verification for F-11 checkout transaction counting."""
from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timedelta

from tests.postgres_test_support import ADMIN_URL, create_postgres_test_schema, drop_postgres_test_schema


@unittest.skipUnless(ADMIN_URL, "TEST_POSTGRES_ADMIN_URL is not configured")
class TransactionCountPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pg = create_postgres_test_schema("cauldra_f11", {"SUPPLY_AI_AUTO_CREATE_SCHEMA": "false"})
        cls.main = cls.pg.main

    @classmethod
    def tearDownClass(cls):
        drop_postgres_test_schema(cls.pg, "cauldra_f11")

    def _tenant(self, label, product_count=3):
        db = self.main.SessionLocal()
        business = self.main.BusinessProfile(
            business_code=f"F11-{uuid.uuid4().hex[:10]}", company_name=label,
            email=f"{uuid.uuid4().hex[:10]}@example.com", subscription_plan="starter",
            billing_interval="monthly",
        )
        db.add(business); db.flush()
        user = self.main.User(
            username=f"admin-{uuid.uuid4().hex[:10]}", password=self.main.hash_password("CountPass9"),
            role="admin", firstname="F11", lastname="Admin",
            email=f"{uuid.uuid4().hex[:8]}@example.com", phone="08000000000", business_id=business.id,
        )
        db.add(user); db.flush()
        db.add(self.main.BusinessSubscription(
            business_id=business.id, plan="starter", billing_interval="monthly", status="active",
            payment_status="paid", current_period_start=datetime.utcnow() - timedelta(days=1),
            current_period_end=datetime.utcnow() + timedelta(days=29),
        ))
        db.add(self.main.Warehouse(business_id=business.id, name="Main Central Warehouse", is_active=True))
        products = []
        for index in range(product_count):
            product = self.main.Product(
                sku=f"SKU-{uuid.uuid4().hex[:10]}", name=f"{label} Product {index + 1}", category="Test",
                quantity=20, initial_stock=20, min_stock_level=0, cost_price=5.0 + index,
                wholesale_price=10.0 + index, retail_price=15.0 + index,
                warehouse="Main Central Warehouse", business_id=business.id, owner_id=user.id,
            )
            db.add(product); db.flush()
            db.add(self.main.WarehouseStock(
                business_id=business.id, product_id=product.id,
                warehouse="Main Central Warehouse", quantity=20,
            ))
            products.append(product.id)
        token = self.main.issue_token(user, db)
        result = business.id, products, token
        db.commit(); db.close()
        return result

    @staticmethod
    def _headers(token):
        return {"Authorization": f"Bearer {token}"}

    def test_multiline_checkout_is_one_transaction_across_live_summary_and_reload(self):
        from fastapi.testclient import TestClient
        business_id, products, token = self._tenant("F11 multiline")
        client = TestClient(self.main.app)
        response = client.post("/sales/checkout", json={"items": [
            {"product_id": products[0], "quantity": 2, "price_mode": "retail"},
            {"product_id": products[1], "quantity": 3, "price_mode": "retail"},
        ]}, headers=self._headers(token))
        self.assertEqual(response.status_code, 200, response.text)
        transaction_id = response.json()["transaction_id"]
        self.assertTrue(transaction_id)
        detail = client.get(f"/sales/transactions/{transaction_id}", headers=self._headers(token))
        live = client.get("/business-days/current-summary", headers=self._headers(token))
        financial = client.get("/financial-summary?period=all", headers=self._headers(token))
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertEqual(len(detail.json()["items"]), 2)
        self.assertEqual(sum(row["quantity"] for row in detail.json()["items"]), 5)
        self.assertEqual(live.json()["transactions"], 1)
        self.assertEqual(live.json()["units_sold"], 5)
        self.assertEqual(financial.json()["transaction_count"], 1)
        self.assertEqual(financial.json()["sale_line_count"], 2)
        self.assertEqual(financial.json()["units_sold"], 5)
        self.assertEqual(financial.json()["sales"], 78.0)
        db = self.main.SessionLocal()
        self.assertEqual(db.query(self.main.SaleTransaction).filter_by(business_id=business_id).count(), 1)
        self.assertEqual(db.query(self.main.SaleModel).filter_by(business_id=business_id).count(), 2)
        self.assertEqual({row.client_ref for row in db.query(self.main.SaleModel).filter_by(business_id=business_id)}, {transaction_id})
        db.close()

    def test_two_checkouts_duplicate_replay_and_closed_history_keep_counts_distinct(self):
        from fastapi.testclient import TestClient
        _, products, token = self._tenant("F11 history")
        client = TestClient(self.main.app)
        first_ref = f"f11-first-{uuid.uuid4().hex}"
        second_ref = f"f11-second-{uuid.uuid4().hex}"
        first_payload = {"items": [
            {"product_id": products[0], "quantity": 1, "price_mode": "retail"},
            {"product_id": products[1], "quantity": 1, "price_mode": "retail"},
        ], "client_ref": first_ref}
        second_payload = {"items": [
            {"product_id": products[2], "quantity": 4, "price_mode": "wholesale"},
        ], "client_ref": second_ref}
        first = client.post("/sales/checkout", json=first_payload, headers=self._headers(token))
        duplicate = client.post("/sales/checkout", json=first_payload, headers=self._headers(token))
        second = client.post("/sales/checkout", json=second_payload, headers=self._headers(token))
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(duplicate.status_code, 200, duplicate.text)
        self.assertTrue(duplicate.json()["duplicate"])
        self.assertEqual(second.status_code, 200, second.text)
        summary = client.get("/financial-summary?period=all", headers=self._headers(token)).json()
        self.assertEqual(summary["transaction_count"], 2)
        self.assertEqual(summary["sale_line_count"], 3)
        self.assertEqual(summary["units_sold"], 6)
        close = client.post("/sales/end-business-day", headers=self._headers(token))
        self.assertEqual(close.status_code, 200, close.text)
        history = client.get("/sales/history?period=all", headers=self._headers(token))
        self.assertEqual(history.status_code, 200, history.text)
        self.assertEqual(len(history.json()), 1)
        self.assertEqual(history.json()[0]["transactions"], 2)
        self.assertEqual(history.json()[0]["items_sold"], 6)

    def test_legacy_unknown_grouping_is_honest_and_tenant_scoped(self):
        business_a, products_a, token_a = self._tenant("F11 legacy A", product_count=1)
        business_b, products_b, token_b = self._tenant("F11 legacy B", product_count=1)
        db = self.main.SessionLocal()
        now = datetime.utcnow()
        for business_id, product_id, quantities in (
            (business_a, products_a[0], (1, 2)),
            (business_b, products_b[0], (7,)),
        ):
            for qty in quantities:
                db.add(self.main.SaleModel(
                    business_id=business_id, product_id=product_id, quantity=qty,
                    total_price=qty * 15.0, unit_price=15.0, unit_cost_at_sale=5.0,
                    product_name_snapshot="Legacy", timestamp=now, client_ref=None,
                ))
        db.commit(); db.close()
        from fastapi.testclient import TestClient
        client = TestClient(self.main.app)
        summary_a = client.get("/financial-summary?period=all", headers=self._headers(token_a)).json()
        summary_b = client.get("/financial-summary?period=all", headers=self._headers(token_b)).json()
        self.assertEqual((summary_a["transaction_count"], summary_a["sale_line_count"], summary_a["units_sold"]), (2, 2, 3))
        self.assertEqual((summary_b["transaction_count"], summary_b["sale_line_count"], summary_b["units_sold"]), (1, 1, 7))


if __name__ == "__main__":
    unittest.main()
