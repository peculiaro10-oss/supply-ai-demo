"""PostgreSQL-backed F-12 verification for immutable and unknown COGS."""
from __future__ import annotations

import json
import unittest
import uuid
from datetime import datetime, timedelta

from sqlalchemy import inspect

from tests.postgres_test_support import ADMIN_URL, create_postgres_test_schema, drop_postgres_test_schema


@unittest.skipUnless(ADMIN_URL, "TEST_POSTGRES_ADMIN_URL is not configured")
class HistoricalCogsPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pg = create_postgres_test_schema("cauldra_f12", {"SUPPLY_AI_AUTO_CREATE_SCHEMA": "false"})
        cls.main = cls.pg.main

    @classmethod
    def tearDownClass(cls):
        drop_postgres_test_schema(cls.pg, "cauldra_f12")

    def _tenant(self, label, cost=10.0, retail=20.0):
        db = self.main.SessionLocal()
        business = self.main.BusinessProfile(
            business_code=f"F12-{uuid.uuid4().hex[:10]}", company_name=label,
            email=f"{uuid.uuid4().hex[:10]}@example.com", subscription_plan="starter",
            billing_interval="monthly",
        )
        db.add(business); db.flush()
        user = self.main.User(
            username=f"admin-{uuid.uuid4().hex[:10]}", password=self.main.hash_password("CogsPass9"),
            role="admin", firstname="F12", lastname="Admin",
            email=f"{uuid.uuid4().hex[:8]}@example.com", phone="08000000000", business_id=business.id,
        )
        db.add(user); db.flush()
        db.add(self.main.BusinessSubscription(
            business_id=business.id, plan="starter", billing_interval="monthly", status="active",
            payment_status="paid", current_period_start=datetime.utcnow() - timedelta(days=1),
            current_period_end=datetime.utcnow() + timedelta(days=29),
        ))
        db.add(self.main.Warehouse(business_id=business.id, name="Main Central Warehouse", is_active=True))
        product = self.main.Product(
            sku=f"SKU-{uuid.uuid4().hex[:10]}", name=f"{label} Product", category="Test",
            quantity=20, initial_stock=20, min_stock_level=0, cost_price=cost,
            wholesale_price=retail - 2, retail_price=retail,
            warehouse="Main Central Warehouse", business_id=business.id, owner_id=user.id,
        )
        db.add(product); db.flush()
        db.add(self.main.WarehouseStock(
            business_id=business.id, product_id=product.id,
            warehouse="Main Central Warehouse", quantity=20,
        ))
        token = self.main.issue_token(user, db)
        result = business.id, product.id, token
        db.commit(); db.close()
        return result

    @staticmethod
    def _headers(token):
        return {"Authorization": f"Bearer {token}"}

    def _legacy_sale(self, business_id, product_id, quantity=2, total=40.0, business_day_id=None):
        db = self.main.SessionLocal()
        sale = self.main.SaleModel(
            business_id=business_id, product_id=product_id, quantity=quantity,
            total_price=total, unit_price=total / quantity, unit_cost_at_sale=None,
            product_name_snapshot="Legacy Snapshot", timestamp=datetime.utcnow(),
            client_ref=None, business_day_id=business_day_id,
        )
        db.add(sale); db.commit(); result = sale.id; db.close()
        return result

    def test_new_sale_cost_snapshot_survives_cost_change_and_product_deletion(self):
        from fastapi.testclient import TestClient
        business_id, product_id, token = self._tenant("F12 known", cost=10.0, retail=20.0)
        client = TestClient(self.main.app)
        checkout = client.post("/sales/checkout", json={"items": [
            {"product_id": product_id, "quantity": 2, "price_mode": "retail"}
        ], "client_ref": f"f12-known-{uuid.uuid4().hex}"}, headers=self._headers(token))
        self.assertEqual(checkout.status_code, 200, checkout.text)
        transaction_id = checkout.json()["transaction_id"]
        before = client.get("/financial-summary?period=all", headers=self._headers(token)).json()
        self.assertEqual((before["cogs"], before["gross_profit"], before["cogs_complete"]), (20.0, 20.0, True))

        db = self.main.SessionLocal()
        product = db.query(self.main.Product).filter_by(id=product_id, business_id=business_id).one()
        product.cost_price = 99.0
        db.commit()
        changed = client.get("/financial-summary?period=all", headers=self._headers(token)).json()
        self.assertEqual((changed["cogs"], changed["gross_profit"]), (20.0, 20.0))
        db.delete(product); db.commit(); db.close()
        deleted = client.get("/financial-summary?period=all", headers=self._headers(token)).json()
        detail = client.get(f"/sales/transactions/{transaction_id}", headers=self._headers(token)).json()
        self.assertEqual((deleted["cogs"], deleted["gross_profit"], deleted["known_cogs"]), (20.0, 20.0, 20.0))
        self.assertEqual(detail["items"][0]["product_name"], "F12 known Product")
        self.assertTrue(detail["items"][0]["unit_cost_known"])
        self.assertFalse(detail["items"][0]["product_exists"])

    def test_legacy_null_cost_stays_unknown_across_live_cost_change_and_deletion(self):
        from fastapi.testclient import TestClient
        business_id, product_id, token = self._tenant("F12 legacy", cost=14.0, retail=20.0)
        sale_id = self._legacy_sale(business_id, product_id, quantity=2, total=40.0)
        client = TestClient(self.main.app)
        before = client.get("/financial-summary?period=all", headers=self._headers(token)).json()
        self.assertIsNone(before["cogs"])
        self.assertIsNone(before["gross_profit"])
        self.assertIsNone(before["net_profit"])
        self.assertFalse(before["cogs_complete"])
        self.assertEqual((before["known_cogs"], before["unknown_cogs_sale_lines"], before["unknown_cogs_sale_units"]), (0.0, 1, 2))

        db = self.main.SessionLocal()
        product = db.query(self.main.Product).filter_by(id=product_id).one()
        product.cost_price = 59.0; db.commit()
        changed = client.get("/financial-summary?period=all", headers=self._headers(token)).json()
        self.assertEqual({k: changed[k] for k in ("cogs", "gross_profit", "net_profit", "known_cogs", "unknown_cogs_sale_lines")},
                         {k: before[k] for k in ("cogs", "gross_profit", "net_profit", "known_cogs", "unknown_cogs_sale_lines")})
        db.delete(product); db.commit(); db.close()
        deleted = client.get("/financial-summary?period=all", headers=self._headers(token)).json()
        detail = client.get(f"/sales/transactions/S{sale_id}", headers=self._headers(token)).json()
        self.assertIsNone(deleted["cogs"])
        self.assertEqual(deleted["known_cogs"], 0.0)
        self.assertEqual(detail["items"][0]["product_name"], "Legacy Snapshot")
        self.assertFalse(detail["items"][0]["unit_cost_known"])
        self.assertFalse(detail["items"][0]["product_exists"])

    def test_refund_of_unknown_legacy_cost_preserves_null_and_audits_uncertainty(self):
        from fastapi.testclient import TestClient
        business_id, product_id, token = self._tenant("F12 refund", cost=18.0, retail=25.0)
        sale_id = self._legacy_sale(business_id, product_id, quantity=2, total=50.0)
        client = TestClient(self.main.app)
        response = client.post(f"/sales/transactions/S{sale_id}/refund", json={
            "lines": [{"sale_id": sale_id, "quantity": 1, "restock": False}],
            "reason": "Other", "note": "Legacy cost unavailable",
            "client_ref": f"f12-refund-{uuid.uuid4().hex}",
        }, headers=self._headers(token))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(response.json()["refund_cost_total"])
        db = self.main.SessionLocal()
        transaction = db.query(self.main.RefundTransaction).filter_by(business_id=business_id).one()
        line = db.query(self.main.RefundLine).filter_by(refund_transaction_id=transaction.id).one()
        audit = db.query(self.main.AuditLog).filter_by(business_id=business_id, action="REFUND_COMPLETED").one()
        self.assertIsNone(transaction.refund_cost_total)
        self.assertIsNone(line.unit_cost)
        self.assertIsNone(line.refund_cost)
        self.assertFalse(json.loads(audit.metadata_json)["cogs_complete"])
        db.close()
        summary = client.get("/financial-summary?period=all", headers=self._headers(token)).json()
        self.assertFalse(summary["cogs_complete"])
        self.assertEqual(summary["unknown_cogs_refund_lines"], 1)
        self.assertIsNone(summary["refunded_cogs"])

    def test_mixed_known_unknown_cost_and_tenant_isolation_are_explicit(self):
        from fastapi.testclient import TestClient
        business_a, product_a, token_a = self._tenant("F12 tenant A", cost=7.0, retail=15.0)
        business_b, product_b, token_b = self._tenant("F12 tenant B", cost=30.0, retail=50.0)
        self._legacy_sale(business_a, product_a, quantity=1, total=15.0)
        client = TestClient(self.main.app)
        known = client.post("/sales/checkout", json={"items": [
            {"product_id": product_a, "quantity": 2, "price_mode": "retail"}
        ]}, headers=self._headers(token_a))
        other = client.post("/sales/checkout", json={"items": [
            {"product_id": product_b, "quantity": 1, "price_mode": "retail"}
        ]}, headers=self._headers(token_b))
        self.assertEqual(known.status_code, 200, known.text)
        self.assertEqual(other.status_code, 200, other.text)
        summary_a = client.get("/financial-summary?period=all", headers=self._headers(token_a)).json()
        summary_b = client.get("/financial-summary?period=all", headers=self._headers(token_b)).json()
        self.assertEqual(summary_a["known_cogs"], 14.0)
        self.assertIsNone(summary_a["cogs"])
        self.assertEqual(summary_a["unknown_cogs_sale_lines"], 1)
        self.assertEqual((summary_b["cogs"], summary_b["known_cogs"], summary_b["cogs_complete"]), (30.0, 30.0, True))

        columns = {column["name"]: column for column in inspect(self.main.engine).get_columns("refund_lines")}
        txn_columns = {column["name"]: column for column in inspect(self.main.engine).get_columns("refund_transactions")}
        self.assertTrue(columns["unit_cost"]["nullable"])
        self.assertTrue(columns["refund_cost"]["nullable"])
        self.assertTrue(txn_columns["refund_cost_total"]["nullable"])


if __name__ == "__main__":
    unittest.main()
