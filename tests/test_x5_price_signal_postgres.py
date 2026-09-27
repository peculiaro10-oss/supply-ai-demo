"""X5 (triage 80): a catalog-mode sale line never charges a submitted
unit_price - and when the till submitted a different price (its catalog copy
was stale) the checkout response now names that line in price_adjustments
instead of ignoring it silently. Sale security and permissions are unchanged.

Set TEST_POSTGRES_ADMIN_URL (see tests/postgres_test_support.py).
"""
from __future__ import annotations

import json
import unittest
import uuid
from datetime import datetime, timedelta

from tests.postgres_test_support import ADMIN_URL, create_postgres_test_schema, drop_postgres_test_schema

PREFIX = "cauldra_x5"


@unittest.skipUnless(ADMIN_URL, "TEST_POSTGRES_ADMIN_URL is not configured")
class PriceSignalPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pg = create_postgres_test_schema(PREFIX, {"SUPPLY_AI_AUTO_CREATE_SCHEMA": "false"})
        cls.main = cls.pg.main

    @classmethod
    def tearDownClass(cls):
        drop_postgres_test_schema(cls.pg, PREFIX)

    def _tenant(self):
        m = self.main
        db = m.SessionLocal()
        business = m.BusinessProfile(business_code=f"X5-{uuid.uuid4().hex[:10]}", company_name="X5 Shop",
                                     country="Nigeria", country_code="NG", currency="NGN",
                                     subscription_plan="starter", billing_interval="monthly")
        db.add(business); db.flush()
        tokens = {}
        for role in ("admin", "staff"):
            user = m.User(username=f"{role}-{uuid.uuid4().hex[:8]}", password=m.hash_password("PriceSignal9"), role=role,
                          firstname="X5", lastname=role.title(), email=f"{role}-{uuid.uuid4().hex[:8]}@example.com",
                          phone="08000000000", business_id=business.id)
            db.add(user); db.flush()
            tokens[role] = m.issue_token(user, db)
            owner_id = user.id if role == "admin" else owner_id
        db.add(m.BusinessSubscription(business_id=business.id, plan="starter", billing_interval="monthly",
                                      status="active", payment_status="paid",
                                      current_period_start=datetime.utcnow() - timedelta(days=1),
                                      current_period_end=datetime.utcnow() + timedelta(days=29)))
        location = m.Location(business_id=business.id, name="Main", is_main=True, is_active=True,
                              country="Nigeria", country_code="NG", currency="NGN", timezone="Africa/Lagos")
        db.add(location); db.flush()
        warehouse = m.Warehouse(business_id=business.id, name="Main Central Warehouse", is_active=True, location_id=location.id)
        db.add(warehouse); db.flush()
        product = m.Product(sku=f"SKU-{uuid.uuid4().hex[:10]}", name="Rice 5kg", category="Test", quantity=20,
                            min_stock_level=0, cost_price=10.0, wholesale_price=15.0, retail_price=20.0,
                            warehouse="Main Central Warehouse", initial_stock=20, business_id=business.id, owner_id=owner_id)
        db.add(product); db.flush()
        db.add(m.WarehouseStock(business_id=business.id, product_id=product.id, warehouse="Main Central Warehouse",
                                warehouse_id=warehouse.id, quantity=20))
        ids = business.id, product.id, location.id
        db.commit(); db.close()
        return ids, tokens

    def test_stale_submitted_price_is_named_and_catalog_price_charged(self):
        from fastapi.testclient import TestClient
        (business_id, product_id, location_id), tokens = self._tenant()
        client = TestClient(self.main.app)
        headers = {"Authorization": f"Bearer {tokens['staff']}"}
        opened = client.post(f"/sales/open-business-day?location_id={location_id}", headers={"Authorization": f"Bearer {tokens['admin']}"})
        self.assertIn(opened.status_code, (200, 201), opened.text)

        def checkout(item):
            return client.post("/sales/checkout", headers=headers, json={
                "items": [{"product_id": product_id, "quantity": 1, **item}],
                "client_ref": f"x5-{uuid.uuid4().hex}", "location_id": location_id})

        stale = checkout({"price_mode": "retail", "unit_price": 18.0})  # the till's older catalog price
        self.assertEqual(stale.status_code, 200, stale.text)
        body = stale.json()
        self.assertEqual(body["daily_total"], 20.0, "the catalog price is charged, as before")
        self.assertEqual(body["price_adjustments"], [{
            "product_id": product_id, "product_name": "Rice 5kg", "price_mode": "retail",
            "submitted_unit_price": 18.0, "charged_unit_price": 20.0}])

        current = checkout({"price_mode": "retail", "unit_price": 20.0})
        self.assertEqual(current.status_code, 200, current.text)
        self.assertEqual(current.json()["price_adjustments"], [], "a matching price says nothing")

        omitted = checkout({"price_mode": "retail"})
        self.assertEqual(omitted.json()["price_adjustments"], [])

        # Permissions unchanged: staff still cannot negotiate a price.
        refused = checkout({"price_mode": "negotiated", "unit_price": 12.0, "negotiated_reason": "Loyal customer"})
        self.assertEqual(refused.status_code, 403, refused.text)

        db = self.main.SessionLocal()
        try:
            audits = [json.loads(a.metadata_json) for a in db.query(self.main.AuditLog)
                      .filter_by(business_id=business_id, action="SALE_COMPLETED").order_by(self.main.AuditLog.id).all()]
        finally:
            db.close()
        self.assertEqual(len(audits), 3)
        self.assertEqual(audits[0]["price_adjustments"][0]["submitted_unit_price"], 18.0)
        self.assertEqual(audits[1]["price_adjustments"], [])


if __name__ == "__main__":
    unittest.main()
