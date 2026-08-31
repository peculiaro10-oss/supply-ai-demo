"""PostgreSQL-backed verification for F-08 authoritative sale pricing."""
from __future__ import annotations

import json
import os
import unittest
import uuid
from datetime import datetime, timedelta
from unittest.mock import patch

from tests.postgres_test_support import (
    ADMIN_URL,
    create_postgres_test_schema,
    drop_postgres_test_schema,
)


@unittest.skipUnless(ADMIN_URL, "TEST_POSTGRES_ADMIN_URL is not configured")
class SalePricingPolicyPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pg = create_postgres_test_schema(
            "cauldra_f08", {"SUPPLY_AI_AUTO_CREATE_SCHEMA": "false"}
        )
        cls.main = cls.pg.main

    @classmethod
    def tearDownClass(cls):
        drop_postgres_test_schema(cls.pg, "cauldra_f08")

    def _tenant(self, label: str):
        db = self.main.SessionLocal()
        business = self.main.BusinessProfile(
            business_code=f"F08-{uuid.uuid4().hex[:10]}", company_name=label,
            email=f"{uuid.uuid4().hex[:10]}@example.com",
            subscription_plan="starter", billing_interval="monthly",
        )
        db.add(business)
        db.flush()
        users = {}
        tokens = {}
        for role in ("admin", "manager", "staff"):
            user = self.main.User(
                username=f"{role}-{uuid.uuid4().hex[:10]}",
                password=self.main.hash_password("PricingPass9"), role=role,
                firstname="F08", lastname=role.title(),
                email=f"{role}-{uuid.uuid4().hex[:8]}@example.com",
                phone="08000000000", business_id=business.id,
            )
            db.add(user)
            db.flush()
            users[role] = user.id
            tokens[role] = self.main.issue_token(user, db)
        db.add(self.main.BusinessSubscription(
            business_id=business.id, plan="starter", billing_interval="monthly",
            status="active", payment_status="paid",
            current_period_start=datetime.utcnow() - timedelta(days=1),
            current_period_end=datetime.utcnow() + timedelta(days=29),
        ))
        db.add(self.main.Warehouse(
            business_id=business.id, name="Main Central Warehouse", is_active=True
        ))
        product = self.main.Product(
            sku=f"SKU-{uuid.uuid4().hex[:10]}", name=f"{label} Product",
            category="Test", quantity=20, min_stock_level=0,
            cost_price=10.0, wholesale_price=15.0, retail_price=20.0,
            warehouse="Main Central Warehouse", initial_stock=20,
            business_id=business.id, owner_id=users["admin"],
        )
        db.add(product)
        db.flush()
        db.add(self.main.WarehouseStock(
            business_id=business.id, product_id=product.id,
            warehouse="Main Central Warehouse", quantity=20,
        ))
        result = business.id, product.id, tokens
        db.commit()
        db.close()
        return result

    @staticmethod
    def _headers(token):
        return {"Authorization": f"Bearer {token}"}

    def _checkout(self, token, product_id, item, reference=None, client=None):
        from fastapi.testclient import TestClient
        request = {
            "items": [{"product_id": product_id, "quantity": 1, **item}],
            "client_ref": reference or f"f08-{uuid.uuid4().hex}",
        }
        return (client or TestClient(self.main.app)).post(
            "/sales/checkout", json=request, headers=self._headers(token)
        )

    def _assert_zero_checkout_writes(self, business_id, reference):
        db = self.main.SessionLocal()
        self.assertEqual(db.query(self.main.SaleTransaction).filter_by(
            business_id=business_id, client_ref=reference).count(), 0)
        self.assertEqual(db.query(self.main.SaleModel).filter_by(
            business_id=business_id, client_ref=reference).count(), 0)
        self.assertEqual(db.query(self.main.BusinessDay).filter_by(
            business_id=business_id).count(), 0)
        self.assertEqual(db.query(self.main.AuditLog).filter_by(
            business_id=business_id, action="SALE_COMPLETED").count(), 0)
        db.close()

    def test_catalog_modes_ignore_submitted_price_and_persist_server_prices(self):
        from fastapi.testclient import TestClient
        business_id, product_id, tokens = self._tenant("F08 catalog")
        client = TestClient(self.main.app)
        retail_ref = f"f08-retail-{uuid.uuid4().hex}"
        wholesale_ref = f"f08-wholesale-{uuid.uuid4().hex}"
        retail = self._checkout(tokens["staff"], product_id, {
            "price_mode": "retail", "unit_price": 0.01,
        }, retail_ref, client)
        wholesale = self._checkout(tokens["staff"], product_id, {
            "price_mode": "wholesale", "unit_price": 99999,
        }, wholesale_ref, client)
        self.assertEqual(retail.status_code, 200, retail.text)
        self.assertEqual(wholesale.status_code, 200, wholesale.text)
        self.assertEqual(retail.json()["daily_total"], 20.0)
        self.assertEqual(wholesale.json()["daily_total"], 15.0)

        for reference, expected in ((retail_ref, 20.0), (wholesale_ref, 15.0)):
            detail = client.get(
                f"/sales/transactions/{reference}", headers=self._headers(tokens["staff"])
            )
            self.assertEqual(detail.status_code, 200, detail.text)
            self.assertEqual(detail.json()["items"][0]["unit_price"], expected)
        db = self.main.SessionLocal()
        rows = db.query(self.main.SaleModel).filter_by(business_id=business_id).all()
        self.assertEqual(sorted(row.unit_price for row in rows), [15.0, 20.0])
        self.assertEqual(sorted(row.total_price for row in rows), [15.0, 20.0])
        audits = db.query(self.main.AuditLog).filter_by(
            business_id=business_id, action="SALE_COMPLETED").all()
        self.assertEqual(len(audits), 2)
        self.assertTrue(all(json.loads(row.metadata_json)["negotiated_lines"] == [] for row in audits))
        db.close()

    def test_manager_negotiation_is_bounded_reasoned_audited_and_idempotent(self):
        business_id, product_id, tokens = self._tenant("F08 negotiated")
        reference = f"f08-negotiated-{uuid.uuid4().hex}"
        item = {"price_mode": "negotiated", "unit_price": 12.5,
                "negotiated_reason": "Approved loyalty discount"}
        first = self._checkout(tokens["manager"], product_id, item, reference)
        replay = self._checkout(tokens["manager"], product_id, item, reference)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertTrue(replay.json()["duplicate"])
        self.assertEqual(first.json()["daily_total"], 12.5)

        db = self.main.SessionLocal()
        self.assertEqual(db.query(self.main.SaleModel).filter_by(
            business_id=business_id, client_ref=reference).count(), 1)
        audit = db.query(self.main.AuditLog).filter_by(
            business_id=business_id, action="SALE_COMPLETED").one()
        metadata = json.loads(audit.metadata_json)
        self.assertEqual(metadata["pricing_policy"], "server_catalog_or_authorized_negotiation")
        self.assertEqual(metadata["negotiated_lines"][0]["negotiated_price"], 12.5)
        self.assertEqual(metadata["negotiated_lines"][0]["reason"], "Approved loyalty discount")
        self.assertEqual(audit.actor_role, "manager")
        db.close()

    def test_staff_negotiation_and_invalid_manager_overrides_leave_no_writes(self):
        business_id, product_id, tokens = self._tenant("F08 rejected")
        cases = [
            (tokens["staff"], 12.0, "Approved loyalty discount", 403),
            (tokens["manager"], 9.99, "Below recorded cost", 400),
            (tokens["manager"], 20.01, "Above catalog ceiling", 400),
            (tokens["manager"], 12.0, "no", 400),
        ]
        for token, price, reason, expected in cases:
            reference = f"f08-rejected-{uuid.uuid4().hex}"
            response = self._checkout(token, product_id, {
                "price_mode": "negotiated", "unit_price": price,
                "negotiated_reason": reason,
            }, reference)
            self.assertEqual(response.status_code, expected, response.text)
            self._assert_zero_checkout_writes(business_id, reference)
        db = self.main.SessionLocal()
        self.assertEqual(db.query(self.main.Product).filter_by(id=product_id).one().quantity, 20)
        db.close()

    def test_failure_rolls_back_negotiated_sale_stock_day_and_audit_then_retry_succeeds(self):
        from fastapi.testclient import TestClient
        business_id, product_id, tokens = self._tenant("F08 rollback")
        reference = f"f08-rollback-{uuid.uuid4().hex}"
        item = {"price_mode": "negotiated", "unit_price": 11.0,
                "negotiated_reason": "Approved damaged packaging"}
        with patch.object(self.main, "add_audit", side_effect=RuntimeError("injected audit failure")):
            failed = self._checkout(
                tokens["admin"], product_id, item, reference,
                TestClient(self.main.app, raise_server_exceptions=False),
            )
        self.assertEqual(failed.status_code, 500)
        self._assert_zero_checkout_writes(business_id, reference)
        db = self.main.SessionLocal()
        self.assertEqual(db.query(self.main.Product).filter_by(id=product_id).one().quantity, 20)
        self.assertEqual(db.query(self.main.WarehouseStock).filter_by(
            product_id=product_id, business_id=business_id).one().quantity, 20)
        db.close()
        retry = self._checkout(tokens["admin"], product_id, item, reference)
        self.assertEqual(retry.status_code, 200, retry.text)
        self.assertEqual(retry.json()["daily_total"], 11.0)

    def test_cross_tenant_product_rejected_without_disclosing_or_mutating_price(self):
        business_a, product_a, tokens_a = self._tenant("F08 tenant A")
        business_b, product_b, _ = self._tenant("F08 tenant B")
        reference = f"f08-tenant-{uuid.uuid4().hex}"
        response = self._checkout(tokens_a["admin"], product_b, {
            "price_mode": "negotiated", "unit_price": 12.0,
            "negotiated_reason": "Cross tenant attempt",
        }, reference)
        self.assertEqual(response.status_code, 409, response.text)
        self._assert_zero_checkout_writes(business_a, reference)
        db = self.main.SessionLocal()
        self.assertEqual(db.query(self.main.Product).filter_by(
            id=product_a, business_id=business_a).one().quantity, 20)
        self.assertEqual(db.query(self.main.Product).filter_by(
            id=product_b, business_id=business_b).one().quantity, 20)
        db.close()


if __name__ == "__main__":
    unittest.main()
