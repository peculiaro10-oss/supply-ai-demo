"""Strict entitlement/downgrade regression matrix.

Uses an isolated in-memory SQLite database for deterministic business-scoped
policy tests. PostgreSQL advisory-lock execution remains a separate opt-in
integration test because it requires a disposable PostgreSQL test database.
"""
from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://test:test@127.0.0.1:5432/cauldra_test")
os.environ.setdefault("SUPPLY_AI_SKIP_DB_STARTUP_CHECK", "true")
os.environ.setdefault("SUPPLY_AI_SECRET_KEY", "test-secret-0123456789abcdef0123456789abcdef0123456789abcdef")

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
import main


class EntitlementReconciliationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
        )
        main.Base.metadata.create_all(cls.engine)
        cls.Session = sessionmaker(bind=cls.engine)

    def setUp(self):
        main.Base.metadata.drop_all(self.engine)
        main.Base.metadata.create_all(self.engine)
        self.db = self.Session()
        self.period_start = datetime(2026, 9, 17, 14, 30)
        self.period_end = datetime(2026, 10, 17, 14, 30)
        self.business = main.BusinessProfile(
            business_code="BIZ-ONE", company_name="One", subscription_plan="starter", billing_interval="monthly",
        )
        self.other_business = main.BusinessProfile(
            business_code="BIZ-TWO", company_name="Two", subscription_plan="starter", billing_interval="monthly",
        )
        self.db.add_all([self.business, self.other_business]); self.db.flush()
        self.db.add_all([
            main.BusinessSubscription(
                business_id=self.business.id, plan="starter", billing_interval="monthly", status="active",
                current_period_start=self.period_start, current_period_end=self.period_end,
            ),
            main.BusinessSubscription(
                business_id=self.other_business.id, plan="starter", billing_interval="monthly", status="active",
                current_period_start=self.period_start, current_period_end=self.period_end,
            ),
        ])
        self.owner = self._user("owner", "admin", False, self.business.id)
        self.other_owner = self._user("other-owner", "admin", False, self.other_business.id)
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def _user(self, username, role, disabled=False, business_id=None):
        row = main.User(
            username=username, password="hash", role=role, firstname="A", lastname="User",
            email=f"{username}@example.test", phone="+2348000000000", business_id=business_id or self.business.id,
            disabled=disabled, auth_version=1,
        )
        self.db.add(row); self.db.flush()
        return row

    def _product(self, index, business_id=None):
        return main.Product(
            sku=f"SKU-{business_id or self.business.id}-{index}", name=f"Product {index}", category="General",
            quantity=0, min_stock_level=1, cost_price=1, retail_price=2,
            business_id=business_id or self.business.id,
        )

    def test_every_public_plan_limit_has_exactly_one_classification(self):
        self.assertEqual(set(main.PLAN_LIMIT_FIELDS), set(main.ENTITLEMENT_LIMIT_TYPES))
        self.assertTrue(set(main.ENTITLEMENT_LIMIT_TYPES.values()) <= {"capacity", "period_usage", "feature_entitlement"})
        for plan in main.PLAN_CONFIG.values():
            self.assertTrue(set(main.PLAN_LIMIT_FIELDS) <= set(plan))

    def test_product_capacity_uses_current_rows_and_frees_immediately(self):
        self.db.bulk_save_objects([self._product(i) for i in range(499)]); self.db.commit()
        self.assertEqual(main.check_capacity_limit(self.db, self.business, "product"), 499)
        self.db.add(self._product(499)); self.db.commit()
        with self.assertRaises(HTTPException) as at_limit:
            main.check_capacity_limit(self.db, self.business, "product")
        self.assertEqual(at_limit.exception.detail["code"], "PLAN_LIMIT_EXCEEDED")
        self.assertEqual(at_limit.exception.detail["current"], 500)
        self.db.bulk_save_objects([self._product(i) for i in range(500, 2000)]); self.db.commit()
        self.assertEqual(self.db.query(main.Product).filter_by(business_id=self.business.id).count(), 2000)
        with self.assertRaises(HTTPException) as over:
            main.check_capacity_limit(self.db, self.business, "product")
        self.assertIn("existing data remains available", over.exception.detail["message"])
        ids = [row[0] for row in self.db.query(main.Product.id).filter_by(business_id=self.business.id).order_by(main.Product.id.desc()).limit(1501)]
        self.db.query(main.Product).filter(main.Product.id.in_(ids)).delete(synchronize_session=False); self.db.commit()
        self.assertEqual(main.check_capacity_limit(self.db, self.business, "product"), 499)

    def test_active_user_seats_enable_and_role_change(self):
        staff = [self._user(f"staff-{i}", "staff") for i in range(3)]
        disabled = self._user("disabled-staff", "staff", True)
        manager = self._user("manager", "manager")
        self.db.commit()
        self.assertEqual(main.get_current_entitlement_usage(self.db, self.business, "staff"), 3)
        with self.assertRaises(HTTPException):
            main.enable_user(disabled.id, self.owner, self.db)
        with self.assertRaises(HTTPException):
            main.change_user_role(staff[0].id, main.UserRoleUpdate(role="manager"), self.owner, self.db)
        staff[0].disabled = True; self.db.commit()
        result = main.enable_user(disabled.id, self.owner, self.db)
        self.assertIn("successfully", result["message"])
        self.assertEqual(main.get_current_entitlement_usage(self.db, self.business, "staff"), 3)
        self.assertEqual(main.get_current_entitlement_usage(self.db, self.business, "admin"), 1, "owner counts as an active Admin")

    def test_location_after_state_normalizes_city_country_and_reactivation(self):
        original = {key: main.PLAN_CONFIG["starter"][key] for key in ("branch", "city", "country")}
        try:
            main.PLAN_CONFIG["starter"].update({"branch": 10, "city": 1, "country": 1})
            lagos = main.Location(business_id=self.business.id, name="Lagos", city=" Lagos ", country_code="ng", is_active=True)
            self.db.add(lagos); self.db.commit()
            main.check_location_after_state(self.db, self.business, exclude_location_id=None, active=True, city="LAGOS", country_code="NG")
            with self.assertRaises(HTTPException) as city_error:
                main.check_location_after_state(self.db, self.business, exclude_location_id=None, active=True, city="Abuja", country_code="NG")
            self.assertEqual(city_error.exception.detail["resource"], "city")
            with self.assertRaises(HTTPException) as country_error:
                main.check_location_after_state(self.db, self.business, exclude_location_id=None, active=True, city="lagos", country_code="GH")
            self.assertEqual(country_error.exception.detail["resource"], "country")
            # Replacing the final city/country is a same-capacity after-state.
            main.check_location_after_state(self.db, self.business, exclude_location_id=lagos.id, active=True, city="Accra", country_code="GH")
            main.PLAN_CONFIG["starter"]["branch"] = 1
            inactive = main.Location(business_id=self.business.id, name="Inactive", city="Lagos", country_code="NG", is_active=False)
            self.db.add(inactive); self.db.commit()
            with self.assertRaises(HTTPException) as branch_error:
                main.check_location_after_state(self.db, self.business, exclude_location_id=inactive.id, active=True, city="Lagos", country_code="NG")
            self.assertEqual(branch_error.exception.detail["resource"], "branch")
        finally:
            main.PLAN_CONFIG["starter"].update(original)

    def test_price_source_deactivation_frees_capacity_without_history_loss(self):
        sources = []
        for i in range(5):
            source = main.PriceMonitorSource(business_id=self.business.id, source_type="manual", is_active=True)
            self.db.add(source); self.db.flush()
            self.db.add(main.PriceHistory(source_id=source.id, price=10 + i)); sources.append(source)
        retired = main.PriceMonitorSource(business_id=self.business.id, source_type="manual", is_active=False)
        self.db.add(retired); self.db.flush(); self.db.add(main.PriceHistory(source_id=retired.id, price=99)); self.db.commit()
        self.assertEqual(main.get_current_entitlement_usage(self.db, self.business, "price_monitor"), 5)
        with self.assertRaises(HTTPException):
            main.check_capacity_limit(self.db, self.business, "price_monitor")
        sources[0].is_active = False; self.db.commit()
        self.assertEqual(main.check_capacity_limit(self.db, self.business, "price_monitor"), 4)
        self.assertEqual(self.db.query(main.PriceHistory).count(), 6)

    def test_supplier_and_warehouse_capacity_reclaims_from_current_state(self):
        self.db.add_all([
            main.Supplier(name=f"Supplier {i}", phone="+2348000000000", business_id=self.business.id)
            for i in range(20)
        ])
        self.db.add_all([
            main.Warehouse(name="One", business_id=self.business.id, is_active=True),
            main.Warehouse(name="Two", business_id=self.business.id, is_active=True),
            main.Warehouse(name="Retired", business_id=self.business.id, is_active=False),
        ])
        self.db.commit()
        with self.assertRaises(HTTPException):
            main.check_capacity_limit(self.db, self.business, "supplier")
        with self.assertRaises(HTTPException):
            main.check_capacity_limit(self.db, self.business, "warehouse")
        supplier = self.db.query(main.Supplier).filter_by(business_id=self.business.id).first()
        self.db.delete(supplier)
        warehouse = self.db.query(main.Warehouse).filter_by(business_id=self.business.id, name="One").one()
        warehouse.is_active = False
        self.db.commit()
        self.assertEqual(main.check_capacity_limit(self.db, self.business, "supplier"), 19)
        self.assertEqual(main.check_capacity_limit(self.db, self.business, "warehouse"), 1)

    def test_storage_is_exact_bytes_and_replacement_reclaims_capacity(self):
        limit = 5 * 1024 ** 3
        self.db.add(main.StoredUpload(
            business_id=self.business.id, kind="test", original_name="large.bin", storage_key="large",
            content_type="application/octet-stream", size_bytes=limit, content_hash="a" * 64,
        )); self.db.commit()
        with self.assertRaises(HTTPException) as error:
            main.check_storage_limit(self.db, self.business, 1)
        self.assertEqual(error.exception.detail["limit"], limit)
        self.assertEqual(main.check_storage_limit(self.db, self.business, 100, reclaimed_bytes=200), limit - 200)

    def test_purchase_orders_use_exact_authoritative_period_and_drafts_do_not_count(self):
        rows = [
            main.PurchaseOrder(business_id=self.business.id, status="SENT", sent_at=self.period_start - timedelta(microseconds=1)),
            main.PurchaseOrder(business_id=self.business.id, status="SENT", sent_at=self.period_start),
            main.PurchaseOrder(business_id=self.business.id, status="DRAFT", sent_at=None),
            main.PurchaseOrder(business_id=self.business.id, status="SENT", sent_at=self.period_end),
        ]
        self.db.add_all(rows); self.db.commit()
        start, end, _ = main.billing_period_for(self.db, self.business)
        self.assertEqual((start, end), (self.period_start, self.period_end))
        self.assertEqual(main.get_current_entitlement_usage(self.db, self.business, "purchase_order"), 1)
        with self.assertRaises(HTTPException):
            main.delete_po(rows[1].id, self.owner, self.db)
        self.assertEqual(main.get_current_entitlement_usage(self.db, self.business, "purchase_order"), 1)
        self.assertEqual(main.add_billing_interval(self.period_start, "monthly"), self.period_end)

    def test_ai_usage_is_period_ledger_and_tenant_scoped(self):
        _, _, period = main.billing_period_for(self.db, self.business)
        self.db.add_all([
            main.AIUsageLedger(business_id=self.business.id, operation_type="chat", credits_consumed=2, billing_period=period, success=True, created_at=self.period_start),
            main.AIUsageLedger(business_id=self.business.id, operation_type="chat", credits_consumed=1, billing_period="2026-09-01", success=True, created_at=self.period_start + timedelta(days=1)),
            main.AIUsageLedger(business_id=self.other_business.id, operation_type="chat", credits_consumed=100, billing_period=period, success=True, created_at=self.period_start),
        ]); self.db.commit()
        self.assertEqual(main.get_current_entitlement_usage(self.db, self.business, "included_ai_credits"), 3)

    def test_downgrade_snapshot_covers_every_capacity_key_without_mutation(self):
        self.db.bulk_save_objects([self._product(i) for i in range(501)]); self.db.commit()
        before = self.db.query(main.Product).filter_by(business_id=self.business.id).count()
        snapshot = main.entitlement_capacity_snapshot(self.db, self.business, "starter")
        self.assertEqual({row["plan_config_key"] for row in snapshot}, set(main.CAPACITY_LIMIT_FIELDS))
        products = next(row for row in snapshot if row["resource"] == "product")
        self.assertEqual(products["status"], "OVER_LIMIT")
        self.assertTrue(all(row["existing_data_preserved"] for row in snapshot))
        self.assertEqual(self.db.query(main.Product).filter_by(business_id=self.business.id).count(), before)

    def test_paid_downgrade_is_scheduled_at_period_end_without_deleting_data(self):
        sub = self.db.query(main.BusinessSubscription).filter_by(business_id=self.business.id).one()
        sub.plan = "business"; self.business.subscription_plan = "business"
        self.db.bulk_save_objects([self._product(i) for i in range(501)]); self.db.commit()
        response = main.schedule_downgrade(
            main.DowngradeRequest(plan="starter", billing_interval="monthly"),
            SimpleNamespace(client=SimpleNamespace(host="127.0.0.1")), self.owner, self.db,
        )
        self.db.refresh(sub)
        self.assertEqual(sub.plan, "business")
        self.assertEqual(sub.pending_downgrade_plan, "starter")
        self.assertEqual(sub.pending_downgrade_effective_at, self.period_end)
        self.assertEqual(self.db.query(main.Product).filter_by(business_id=self.business.id).count(), 501)
        product_impact = next(row for row in response["downgrade_impact"]["capacity_impact"] if row["resource"] == "product")
        self.assertEqual(product_impact["status"], "OVER_LIMIT")

    def test_trial_can_select_lower_plan_while_over_limit_without_data_cleanup(self):
        sub = self.db.query(main.BusinessSubscription).filter_by(business_id=self.business.id).one()
        sub.plan = "business"; sub.status = "trialing"; self.business.subscription_plan = "business"
        self.db.bulk_save_objects([self._product(i) for i in range(501)]); self.db.commit()
        response = main.change_plan(
            main.ChangePlanRequest(plan="starter", billing_interval="monthly"),
            SimpleNamespace(client=SimpleNamespace(host="127.0.0.2")), self.owner, self.db,
        )
        self.db.refresh(sub)
        self.assertEqual(sub.plan, "starter")
        self.assertEqual(self.db.query(main.Product).filter_by(business_id=self.business.id).count(), 501)
        self.assertEqual(response["downgrade_impact"]["to_plan"], "starter")

    def test_unlimited_none_never_becomes_zero(self):
        sub = self.db.query(main.BusinessSubscription).filter_by(business_id=self.business.id).one()
        sub.plan = "enterprise"; self.db.commit()
        for i in range(20):
            self._user(f"manager-unlimited-{i}", "manager")
        self.db.commit()
        self.assertIsNone(main.get_plan_limit(self.db, self.business, "manager"))
        self.assertEqual(main.check_capacity_limit(self.db, self.business, "manager"), 20)

    def test_other_business_never_consumes_capacity(self):
        self.db.bulk_save_objects([self._product(i, self.other_business.id) for i in range(600)]); self.db.commit()
        self.assertEqual(main.get_current_entitlement_usage(self.db, self.business, "product"), 0)
        self.assertEqual(main.check_capacity_limit(self.db, self.business, "product"), 0)

    def test_openapi_exposes_impact_and_reactivation_routes(self):
        paths = main.app.openapi()["paths"]
        self.assertIn("/subscription/downgrade-impact", paths)
        self.assertIn("/price-monitor/sources/{source_id}", paths)
        self.assertIn("patch", paths["/users/{user_id}/role"])

    def test_postgres_lock_path_is_present_and_transaction_scoped(self):
        source = Path(main.__file__).read_text(encoding="utf-8")
        self.assertIn("pg_advisory_xact_lock", source)
        self.assertIn("business.id, resource", source)


if __name__ == "__main__":
    unittest.main()
