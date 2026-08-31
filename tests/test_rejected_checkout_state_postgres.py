"""PostgreSQL-backed F-10 verification: rejected checkout creates no state."""
from __future__ import annotations

import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from tests.postgres_test_support import ADMIN_URL, create_postgres_test_schema, drop_postgres_test_schema


@unittest.skipUnless(ADMIN_URL, "TEST_POSTGRES_ADMIN_URL is not configured")
class RejectedCheckoutStatePostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pg = create_postgres_test_schema("cauldra_f10", {"SUPPLY_AI_AUTO_CREATE_SCHEMA": "false"})
        cls.main = cls.pg.main

    @classmethod
    def tearDownClass(cls):
        drop_postgres_test_schema(cls.pg, "cauldra_f10")

    def _tenant(self, label, quantity=2):
        db = self.main.SessionLocal()
        business = self.main.BusinessProfile(
            business_code=f"F10-{uuid.uuid4().hex[:10]}", company_name=label,
            email=f"{uuid.uuid4().hex[:10]}@example.com", subscription_plan="starter",
            billing_interval="monthly",
        )
        db.add(business); db.flush()
        user = self.main.User(
            username=f"admin-{uuid.uuid4().hex[:10]}", password=self.main.hash_password("CheckoutPass9"),
            role="admin", firstname="F10", lastname="Admin",
            email=f"{uuid.uuid4().hex[:8]}@example.com", phone="08000000000",
            business_id=business.id,
        )
        db.add(user); db.flush()
        db.add(self.main.BusinessSubscription(
            business_id=business.id, plan="starter", billing_interval="monthly",
            status="active", payment_status="paid",
            current_period_start=datetime.utcnow() - timedelta(days=1),
            current_period_end=datetime.utcnow() + timedelta(days=29),
        ))
        db.add(self.main.Warehouse(business_id=business.id, name="Main Central Warehouse", is_active=True))
        product = self.main.Product(
            sku=f"SKU-{uuid.uuid4().hex[:10]}", name=f"{label} Product", category="Test",
            quantity=quantity, initial_stock=quantity, min_stock_level=0,
            cost_price=10.0, wholesale_price=15.0, retail_price=20.0,
            warehouse="Main Central Warehouse", business_id=business.id, owner_id=user.id,
        )
        db.add(product); db.flush()
        db.add(self.main.WarehouseStock(
            business_id=business.id, product_id=product.id,
            warehouse="Main Central Warehouse", quantity=quantity,
        ))
        token = self.main.issue_token(user, db)
        result = business.id, product.id, token
        db.commit(); db.close()
        return result

    @staticmethod
    def _headers(token):
        return {"Authorization": f"Bearer {token}"}

    def _assert_no_checkout_state(self, business_id, reference=None):
        db = self.main.SessionLocal()
        self.assertEqual(db.query(self.main.BusinessDay).filter_by(business_id=business_id).count(), 0)
        q = db.query(self.main.SaleTransaction).filter_by(business_id=business_id)
        if reference is not None:
            q = q.filter_by(client_ref=reference)
        self.assertEqual(q.count(), 0)
        self.assertEqual(db.query(self.main.SaleModel).filter_by(business_id=business_id).count(), 0)
        self.assertEqual(db.query(self.main.AuditLog).filter(
            self.main.AuditLog.business_id == business_id,
            self.main.AuditLog.action.in_(["BUSINESS_DAY_AUTO_OPENED", "SALE_COMPLETED"]),
        ).count(), 0)
        db.close()

    def test_empty_and_request_validation_failures_create_zero_durable_state(self):
        from fastapi.testclient import TestClient
        business_id, product_id, token = self._tenant("F10 empty")
        client = TestClient(self.main.app)
        empty = client.post("/sales/checkout", json={"items": [], "client_ref": "f10-empty"}, headers=self._headers(token))
        invalid = client.post("/sales/checkout", json={"items": [{
            "product_id": product_id, "quantity": "not-an-integer", "price_mode": "retail"
        }], "client_ref": "f10-invalid"}, headers=self._headers(token))
        self.assertEqual(empty.status_code, 400, empty.text)
        self.assertEqual(invalid.status_code, 422, invalid.text)
        self._assert_no_checkout_state(business_id)

    def test_stock_pricing_and_foreign_tenant_rejections_create_zero_durable_state(self):
        from fastapi.testclient import TestClient
        business_a, product_a, token_a = self._tenant("F10 tenant A", quantity=1)
        business_b, product_b, _ = self._tenant("F10 tenant B", quantity=5)
        client = TestClient(self.main.app)
        cases = [
            ("f10-stock", {"product_id": product_a, "quantity": 2, "price_mode": "retail"}, 409),
            ("f10-price", {"product_id": product_a, "quantity": 1, "price_mode": "negotiated", "unit_price": 5, "negotiated_reason": "Below cost"}, 400),
            ("f10-tenant", {"product_id": product_b, "quantity": 1, "price_mode": "retail"}, 409),
        ]
        for reference, item, expected in cases:
            response = client.post("/sales/checkout", json={"items": [item], "client_ref": reference}, headers=self._headers(token_a))
            self.assertEqual(response.status_code, expected, response.text)
        self._assert_no_checkout_state(business_a)
        db = self.main.SessionLocal()
        self.assertEqual(db.query(self.main.Product).filter_by(id=product_a, business_id=business_a).one().quantity, 1)
        self.assertEqual(db.query(self.main.Product).filter_by(id=product_b, business_id=business_b).one().quantity, 5)
        self.assertEqual(db.query(self.main.BusinessDay).filter_by(business_id=business_b).count(), 0)
        db.close()

    def test_concurrent_rejected_requests_leave_no_claim_day_sale_or_audit(self):
        from fastapi.testclient import TestClient
        business_id, product_id, token = self._tenant("F10 concurrent", quantity=1)

        def submit(index):
            return TestClient(self.main.app).post("/sales/checkout", json={
                "items": [{"product_id": product_id, "quantity": 2, "price_mode": "retail"}],
                "client_ref": f"f10-concurrent-{index}",
            }, headers=self._headers(token)).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(submit, range(2)))
        self.assertEqual(statuses, [409, 409])
        self._assert_no_checkout_state(business_id)

    def test_rejected_reference_can_retry_successfully_and_duplicate_is_read_only(self):
        from fastapi.testclient import TestClient
        business_id, product_id, token = self._tenant("F10 retry", quantity=2)
        reference = f"f10-retry-{uuid.uuid4().hex}"
        client = TestClient(self.main.app)
        rejected = client.post("/sales/checkout", json={
            "items": [{"product_id": product_id, "quantity": 3, "price_mode": "retail"}],
            "client_ref": reference,
        }, headers=self._headers(token))
        self.assertEqual(rejected.status_code, 409, rejected.text)
        self._assert_no_checkout_state(business_id, reference)

        payload = {"items": [{"product_id": product_id, "quantity": 1, "price_mode": "retail"}], "client_ref": reference}
        success = client.post("/sales/checkout", json=payload, headers=self._headers(token))
        duplicate = client.post("/sales/checkout", json=payload, headers=self._headers(token))
        self.assertEqual(success.status_code, 200, success.text)
        self.assertEqual(duplicate.status_code, 200, duplicate.text)
        self.assertTrue(duplicate.json()["duplicate"])
        detail = client.get(f"/sales/transactions/{reference}", headers=self._headers(token))
        self.assertEqual(detail.status_code, 200, detail.text)
        db = self.main.SessionLocal()
        self.assertEqual(db.query(self.main.BusinessDay).filter_by(business_id=business_id).count(), 1)
        self.assertEqual(db.query(self.main.SaleTransaction).filter_by(business_id=business_id, client_ref=reference).count(), 1)
        self.assertEqual(db.query(self.main.SaleModel).filter_by(business_id=business_id, client_ref=reference).count(), 1)
        self.assertEqual(db.query(self.main.AuditLog).filter_by(business_id=business_id, action="BUSINESS_DAY_AUTO_OPENED").count(), 1)
        self.assertEqual(db.query(self.main.AuditLog).filter_by(business_id=business_id, action="SALE_COMPLETED").count(), 1)
        self.assertEqual(db.query(self.main.Product).filter_by(id=product_id).one().quantity, 1)
        db.close()


if __name__ == "__main__":
    unittest.main()
