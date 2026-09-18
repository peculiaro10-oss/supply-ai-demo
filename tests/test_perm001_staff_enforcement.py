"""PERM-001 Phase A — permission enforcement across the six audited areas.

Covers, for Admin / Manager / Staff-default / Staff-with-grant /
Staff-with-denial:

* `POST /products/`            — effective `inventory.add_product` (D13: Staff
                                 allowed by default, explicit denial = 403)
* `GET /expenses/`             — `expenses.view` = own rows only,
                                 `expenses.view_all` = all rows, across every
                                 filter, the `total`, and the exports
* `GET /sales/analytics`       — `reports.sales`
* `GET /suppliers/`            — `supplier.view`
* `GET /warehouses/`           — `warehouse.view`
* `GET /warehouses/operational`— minimal projection, exact field set
* `GET /offline/snapshot`      — the second read path to the same data
* `POST /offline/replay`       — `product_create` honours the same permission
* Tenant isolation             — Tenant B's data is never reachable

Business Brain is deliberately NOT asserted here: `business_brain.view_full`
is a content-depth permission, not an endpoint 403 (see the plan §4.3), and
its Staff payload is captured during the QA re-audit rather than pinned by a
unit test that would have to refresh Brain state.

PostgreSQL-backed (skipped unless TEST_POSTGRES_ADMIN_URL is configured —
see tests/postgres_test_support.py): one disposable schema for the whole
module, dropped in tearDownModule even on failure.
"""
from __future__ import annotations

import json
import os
import unittest
import uuid
from datetime import datetime, timedelta, timezone

from postgres_test_support import ADMIN_URL, create_postgres_test_schema, drop_postgres_test_schema

_ctx = None
main = None
_previous_signing_key = None
_product_seq = 0


def _install_test_offline_signing_key():
    """A throwaway P-256 key for this module only, exactly as
    tests/test_offline_access.py does. Offline provisioning returns 503 without
    one, which previously made the offline cases SKIP; the test process must
    never inherit a real environment's key either."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    previous = os.environ.get("CAULDRA_OFFLINE_SIGNING_KEY")
    os.environ["CAULDRA_OFFLINE_SIGNING_KEY"] = ec.generate_private_key(ec.SECP256R1()).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
    ).decode()
    return previous


def _restore_offline_signing_key(previous):
    if previous is None:
        os.environ.pop("CAULDRA_OFFLINE_SIGNING_KEY", None)
    else:
        os.environ["CAULDRA_OFFLINE_SIGNING_KEY"] = previous


def setUpModule():
    global _ctx, main, _previous_signing_key
    if not ADMIN_URL:
        return
    _previous_signing_key = _install_test_offline_signing_key()
    _ctx = create_postgres_test_schema("cauldra_perm001")
    main = _ctx.main


def tearDownModule():
    try:
        if _ctx is not None:
            drop_postgres_test_schema(_ctx, "cauldra_perm001")
    finally:
        if ADMIN_URL:
            _restore_offline_signing_key(_previous_signing_key)


@unittest.skipUnless(ADMIN_URL, "TEST_POSTGRES_ADMIN_URL is not configured")
class Perm001EnforcementTests(unittest.TestCase):
    """One business (Tenant A) with all four access states, plus Tenant B."""

    def setUp(self):
        from fastapi.testclient import TestClient

        suffix = uuid.uuid4().hex[:10]
        self.suffix = suffix
        db = main.SessionLocal()

        biz_a = main.BusinessProfile(business_code=f"P1-A-{suffix}", company_name="PERM001 A")
        biz_b = main.BusinessProfile(business_code=f"P1-B-{suffix}", company_name="PERM001 B")
        db.add_all([biz_a, biz_b])
        db.flush()

        # Real registration creates a Main Location; the ORM shortcut here
        # did not, which previously SKIPPED the location_id expense filter.
        loc_a = main.Location(business_id=biz_a.id, name="Main Location", is_main=True, is_active=True)
        loc_b = main.Location(business_id=biz_b.id, name="Main Location", is_main=True, is_active=True)
        db.add_all([loc_a, loc_b])
        db.flush()

        wh_a = main.Warehouse(business_id=biz_a.id, name="Main Central Warehouse", is_active=True)
        wh_a2 = main.Warehouse(business_id=biz_a.id, name="Secondary Warehouse", is_active=True)
        wh_a_inactive = main.Warehouse(business_id=biz_a.id, name="Retired Warehouse", is_active=False)
        wh_b = main.Warehouse(business_id=biz_b.id, name="Tenant B Warehouse", is_active=True)
        db.add_all([wh_a, wh_a2, wh_a_inactive, wh_b])

        def user(biz, role, label, password):
            return main.User(
                username=f"{label} {suffix}", password=main.hash_password(password), role=role,
                email=f"{label.lower().replace(' ', '')}-{suffix}@test.com", phone="1",
                business_id=biz.id, disabled=False,
            )

        admin_a = user(biz_a, "admin", "Admin A", "AdminPass9")
        manager_a = user(biz_a, "manager", "Manager A", "ManagerPass9")
        staff_a = user(biz_a, "staff", "Staff A", "StaffPass9")
        staff_grant_a = user(biz_a, "staff", "Staff Grant A", "StaffPass9")
        staff_deny_a = user(biz_a, "staff", "Staff Deny A", "StaffPass9")
        admin_b = user(biz_b, "admin", "Admin B", "AdminPass9")
        db.add_all([admin_a, manager_a, staff_a, staff_grant_a, staff_deny_a, admin_b])
        db.flush()

        # Every code the "granted" Staff member needs for the positive cases.
        staff_grant_a.permission_overrides = json.dumps({
            "expenses.view_all": True, "reports.sales": True,
            "supplier.view": True, "warehouse.view": True,
        })
        # The revoked Staff member: add_product taken away from the default.
        staff_deny_a.permission_overrides = json.dumps({"inventory.add_product": False})

        db.add_all([
            main.Supplier(business_id=biz_a.id, name="Tenant A Supplier", contact_email="a@sup.test", phone="100"),
            main.Supplier(business_id=biz_b.id, name="Tenant B Supplier", contact_email="b@sup.test", phone="200"),
        ])
        db.flush()

        # Expenses: two owned by Staff A, three by Admin A, one in Tenant B.
        now = datetime.utcnow()
        db.add_all([
            main.Expense(business_id=biz_a.id, category="Transport", amount=1000.0, owner_id=staff_a.id, created_at=now - timedelta(days=1), note="staff own one", location_id=loc_a.id),
            main.Expense(business_id=biz_a.id, category="Fuel", amount=2000.0, owner_id=staff_a.id, created_at=now - timedelta(days=2), note="staff own two"),
            main.Expense(business_id=biz_a.id, category="Rent", amount=50000.0, owner_id=admin_a.id, created_at=now - timedelta(days=1), note="admin one", location_id=loc_a.id),
            main.Expense(business_id=biz_a.id, category="Transport", amount=3000.0, owner_id=admin_a.id, created_at=now - timedelta(days=3), note="admin two"),
            main.Expense(business_id=biz_a.id, category="Power", amount=4000.0, owner_id=manager_a.id, created_at=now - timedelta(days=4), note="manager one", location_id=loc_a.id),
            main.Expense(business_id=biz_b.id, category="Rent", amount=9999.0, owner_id=admin_b.id, created_at=now - timedelta(days=1), note="tenant b"),
        ])
        db.commit()

        client = TestClient(main.app)

        def employee_token(business_code, username, password, role):
            r = client.post("/auth/employee-login", json={
                "business_id": business_code, "username": username, "password": password, "selected_role": role,
            })
            assert r.status_code == 200, r.text
            return r.json()["access_token"]

        def admin_token(business_code, username, password):
            r = client.post("/auth/admin-login", json={
                "business_id": business_code, "username": username, "password": password,
            })
            assert r.status_code == 200, r.text
            return r.json()["access_token"]

        self.db, self.client = db, client
        self.biz_a, self.biz_b = biz_a, biz_b
        self.wh_a, self.wh_a2, self.wh_a_inactive = wh_a, wh_a2, wh_a_inactive
        self.admin_a, self.manager_a = admin_a, manager_a
        self.staff_a, self.staff_grant_a, self.staff_deny_a = staff_a, staff_grant_a, staff_deny_a
        self.admin_b = admin_b
        self.loc_a = loc_a

        self.t_admin = admin_token(biz_a.business_code, admin_a.username, "AdminPass9")
        self.t_manager = employee_token(biz_a.business_code, manager_a.username, "ManagerPass9", "manager")
        self.t_staff = employee_token(biz_a.business_code, staff_a.username, "StaffPass9", "staff")
        self.t_staff_grant = employee_token(biz_a.business_code, staff_grant_a.username, "StaffPass9", "staff")
        self.t_staff_deny = employee_token(biz_a.business_code, staff_deny_a.username, "StaffPass9", "staff")
        self.t_admin_b = admin_token(biz_b.business_code, admin_b.username, "AdminPass9")

    def tearDown(self):
        self.db.close()

    # ------------------------------------------------------------------ utils
    def auth(self, token):
        return {"Authorization": f"Bearer {token}"}

    def get(self, path, token, **params):
        return self.client.get(path, headers=self.auth(token), params=params or None)

    def product_payload(self, **overrides):
        # Every generated product is genuinely distinct. Identical size and
        # prices made the real duplicate detector (GC-011) answer 409
        # "possible duplicate" on the ALLOWED cases - a correct product rule,
        # so the fixture changes rather than overriding the duplicate check.
        global _product_seq
        _product_seq += 1
        n = _product_seq
        payload = {
            "name": f"Widget {uuid.uuid4().hex[:6]}", "category": "General", "size": f"{n * 7} unit",
            "warehouse": "Main Central Warehouse", "quantity": 5, "min_stock_level": 1,
            "cost_price": 5.0 + n * 13, "wholesale_price": 8.0 + n * 17, "retail_price": 12.0 + n * 23,
        }
        payload.update(overrides)
        return payload

    # ------------------------------------------- area 1: inventory.add_product
    def test_add_product_matrix(self):
        cases = [
            (self.t_admin, 200, "admin"),
            (self.t_manager, 200, "manager"),
            (self.t_staff, 200, "staff default (D13 allows)"),
            (self.t_staff_grant, 200, "staff explicit grant"),
            (self.t_staff_deny, 403, "staff explicit denial"),
        ]
        for token, expected, label in cases:
            with self.subTest(case=label):
                r = self.client.post("/products/", json=self.product_payload(), headers=self.auth(token))
                self.assertEqual(r.status_code, expected, f"{label}: {r.text}")

    def test_denied_staff_add_product_has_no_side_effects(self):
        client_ref = f"perm001-denied-{self.suffix}"
        before_rows = self.db.query(main.Product).filter(main.Product.business_id == self.biz_a.id).count()
        before_usage = main.get_current_entitlement_usage(self.db, self.biz_a, "product")
        r = self.client.post(
            "/products/", json=self.product_payload(name="Denied Row", client_ref=client_ref),
            headers=self.auth(self.t_staff_deny),
        )
        self.assertEqual(r.status_code, 403, r.text)
        self.assertEqual(self.db.query(main.Product).filter(main.Product.business_id == self.biz_a.id).count(), before_rows)
        self.assertIsNone(self.db.query(main.MutationIdempotency).filter(
            main.MutationIdempotency.business_id == self.biz_a.id,
            main.MutationIdempotency.operation == "product_create",
            main.MutationIdempotency.client_ref == client_ref,
        ).first())
        self.assertEqual(main.get_current_entitlement_usage(self.db, self.biz_a, "product"), before_usage)

    # ------------------------------------------------- area 2: expenses scoping
    def _expense_ids(self, body):
        return {row["id"] for row in body["expenses"]}

    def _own_expense_ids(self, owner):
        return {
            e.id for e in self.db.query(main.Expense).filter(
                main.Expense.business_id == self.biz_a.id, main.Expense.owner_id == owner.id,
            ).all()
        }

    def test_staff_expenses_list_is_own_rows_only_with_matching_total(self):
        r = self.get("/expenses/", self.t_staff)
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        own = self._own_expense_ids(self.staff_a)
        self.assertEqual(self._expense_ids(body), own)
        self.assertEqual(body["total"], len(own))
        for row in body["expenses"]:
            self.assertEqual(row["owner_id"], self.staff_a.id)

    def test_admin_and_manager_see_every_expense(self):
        all_ids = {
            e.id for e in self.db.query(main.Expense).filter(main.Expense.business_id == self.biz_a.id).all()
        }
        for token, label in ((self.t_admin, "admin"), (self.t_manager, "manager")):
            with self.subTest(role=label):
                r = self.get("/expenses/", token)
                self.assertEqual(r.status_code, 200, r.text)
                body = r.json()
                self.assertEqual(self._expense_ids(body), all_ids)
                self.assertEqual(body["total"], len(all_ids))

    def test_staff_with_view_all_grant_sees_every_expense(self):
        r = self.get("/expenses/", self.t_staff_grant)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(
            r.json()["total"],
            self.db.query(main.Expense).filter(main.Expense.business_id == self.biz_a.id).count(),
        )

    def test_staff_expense_scoping_survives_every_filter(self):
        own = self._own_expense_ids(self.staff_a)
        today = datetime.utcnow().date().isoformat()
        long_ago = (datetime.utcnow() - timedelta(days=365)).date().isoformat()
        filters = [
            {"category": "Transport"},
            {"category": "Rent"},                    # only an Admin-owned row has this
            {"search": "admin"},                     # note text of rows they must not see
            {"date_from": long_ago, "date_to": today},
            {"limit": 1},
            {"limit": 1, "offset": 1},
            {"user_id": self.staff_a.id},            # explicitly asking for self is allowed
        ]
        for params in filters:
            with self.subTest(params=params):
                r = self.get("/expenses/", self.t_staff, **params)
                self.assertEqual(r.status_code, 200, r.text)
                body = r.json()
                self.assertTrue(self._expense_ids(body) <= own, f"leaked rows for {params}: {body}")
                self.assertLessEqual(body["total"], len(own))
                for row in body["expenses"]:
                    self.assertEqual(row["owner_id"], self.staff_a.id)

    def test_staff_cannot_request_another_users_expenses(self):
        r = self.get("/expenses/", self.t_staff, user_id=self.admin_a.id)
        self.assertEqual(r.status_code, 403, r.text)

    def test_staff_expense_location_filter_stays_own_scoped(self):
        r = self.get("/expenses/", self.t_staff, location_id=self.loc_a.id)
        self.assertEqual(r.status_code, 200, r.text)
        ids = self._expense_ids(r.json())
        self.assertTrue(ids <= self._own_expense_ids(self.staff_a))
        # Non-vacuous: the Location also holds Admin and Manager rows, and
        # exactly one of Staff A's own rows - that one and only that one.
        self.assertEqual(len(ids), 1, r.text)

    def test_expense_exports_use_the_same_scope(self):
        # Staff default has neither expenses.export nor view_all.
        self.assertEqual(self.get("/expenses/export", self.t_staff).status_code, 403)
        # A Staff member granted export but NOT view_all must still only be
        # able to export their own rows.
        self.staff_a.permission_overrides = json.dumps({"expenses.export": True})
        self.db.commit(); self.db.expire_all()
        r = self.get("/expenses/export", self.t_staff)
        self.assertEqual(r.status_code, 200, r.text)
        body = r.text
        self.assertIn("staff own one", body)
        self.assertNotIn("admin one", body)
        self.assertNotIn("manager one", body)

    def test_expense_adjustments_remain_view_all_only(self):
        expense = self.db.query(main.Expense).filter(
            main.Expense.business_id == self.biz_a.id, main.Expense.owner_id == self.staff_a.id,
        ).first()
        self.assertEqual(self.get(f"/expenses/{expense.id}/adjustments", self.t_staff).status_code, 403)
        self.assertEqual(self.get(f"/expenses/{expense.id}/adjustments", self.t_admin).status_code, 200)

    # --------------------------------------------------- area 4: reports.sales
    def test_sales_analytics_requires_reports_sales(self):
        self.assertEqual(self.get("/sales/analytics", self.t_admin).status_code, 200)
        self.assertEqual(self.get("/sales/analytics", self.t_manager).status_code, 200)
        self.assertEqual(self.get("/sales/analytics", self.t_staff).status_code, 403)
        self.assertEqual(self.get("/sales/analytics", self.t_staff_grant).status_code, 200)

    def test_sales_export_guard_is_unchanged(self):
        # /sales/export is gated by sales.view_history, NOT reports.sales —
        # granting reports.sales alone must not open it.
        self.assertEqual(self.get("/sales/export", self.t_staff_grant).status_code, 403)

    # ------------------------------------------------- area 5: supplier.view
    def test_supplier_directory_requires_supplier_view(self):
        self.assertEqual(self.get("/suppliers/", self.t_admin).status_code, 200)
        self.assertEqual(self.get("/suppliers/", self.t_manager).status_code, 200)
        self.assertEqual(self.get("/suppliers/", self.t_staff).status_code, 403)
        granted = self.get("/suppliers/", self.t_staff_grant)
        self.assertEqual(granted.status_code, 200, granted.text)
        self.assertEqual({s["name"] for s in granted.json()}, {"Tenant A Supplier"})

    # ------------------------------------------------ area 6: warehouse.view
    def test_warehouse_directory_requires_warehouse_view(self):
        self.assertEqual(self.get("/warehouses/", self.t_admin).status_code, 200)
        self.assertEqual(self.get("/warehouses/", self.t_manager).status_code, 200)
        self.assertEqual(self.get("/warehouses/", self.t_staff).status_code, 403)
        self.assertEqual(self.get("/warehouses/", self.t_staff_grant).status_code, 200)

    def test_operational_warehouse_endpoint_exposes_only_identity(self):
        r = self.get("/warehouses/operational", self.t_staff)
        self.assertEqual(r.status_code, 200, r.text)
        rows = r.json()
        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(set(row.keys()), {"id", "name", "location_id"})
        names = {row["name"] for row in rows}
        self.assertIn("Main Central Warehouse", names)
        self.assertNotIn("Retired Warehouse", names)   # active only
        self.assertNotIn("Tenant B Warehouse", names)  # business-scoped

    def test_operational_warehouse_endpoint_is_permission_gated(self):
        for token in (self.t_admin, self.t_manager, self.t_staff, self.t_staff_grant):
            self.assertEqual(self.get("/warehouses/operational", token).status_code, 200)
        # Deny both operational permissions and the endpoint closes too.
        self.staff_a.permission_overrides = json.dumps({"inventory.view": False, "sales.create": False})
        self.db.commit(); self.db.expire_all()
        self.assertEqual(self.get("/warehouses/operational", self.t_staff).status_code, 403)

    def test_guest_gets_401_before_any_permission_check(self):
        for path in ("/expenses/", "/sales/analytics", "/suppliers/", "/warehouses/", "/warehouses/operational"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 401)

    # ------------------------------------------------ area 7: offline snapshot
    def test_offline_snapshot_respects_the_same_permissions(self):
        r = self.get("/offline/snapshot", self.t_staff)
        self.assertEqual(r.status_code, 200, r.text)
        snap = r.json()
        self.assertEqual(snap["suppliers"], [])
        self.assertEqual(snap["freshness"].get("/suppliers/"), "permission unavailable")
        self.assertEqual(snap["freshness"].get("/warehouses/"), "operational projection")
        for row in snap["warehouses"]:
            self.assertEqual(set(row.keys()), {"id", "name", "location_id"})
        own = self._own_expense_ids(self.staff_a)
        self.assertTrue({e["id"] for e in snap["expenses"]} <= own)

    def test_offline_snapshot_for_privileged_roles_is_unchanged(self):
        r = self.get("/offline/snapshot", self.t_admin)
        self.assertEqual(r.status_code, 200, r.text)
        snap = r.json()
        self.assertTrue(snap["suppliers"])
        self.assertNotIn("/suppliers/", snap["freshness"])
        self.assertIn("sku_count", snap["warehouses"][0])
        self.assertEqual(
            {e["id"] for e in snap["expenses"]},
            {e.id for e in self.db.query(main.Expense).filter(main.Expense.business_id == self.biz_a.id).all()},
        )

    def test_offline_snapshot_preserves_the_plan009_business_payload(self):
        snap = self.get("/offline/snapshot", self.t_staff).json()
        self.assertIn("business", snap)
        self.assertIn("ai_included", snap["business"])

    # -------------------------------------------------- area 8: offline replay
    def _provision_device(self, token):
        device_id = str(uuid.uuid4())
        r = self.client.post("/offline/provision", json={"device_id": device_id}, headers=self.auth(token))
        if r.status_code == 503:
            self.skipTest("CAULDRA_OFFLINE_SIGNING_KEY is not configured")
        self.assertEqual(r.status_code, 200, r.text)
        return device_id

    def _replay_product_create(self, token, device_id, user_row, op_id=None):
        return self.client.post("/offline/replay", headers=self.auth(token), json={
            "schema_version": 2, "op_id": op_id or str(uuid.uuid4()), "device_id": device_id,
            "user_id": user_row.id, "business_id": self.biz_a.id,
            "auth_version": int(user_row.auth_version or 1), "type": "product_create",
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "payload": self.product_payload(name=f"Offline {uuid.uuid4().hex[:6]}"),
        })

    def test_offline_replay_product_create_follows_the_effective_permission(self):
        device_id = self._provision_device(self.t_staff)
        allowed = self._replay_product_create(self.t_staff, device_id, self.staff_a)
        self.assertEqual(allowed.status_code, 200, allowed.text)

        denied_device = self._provision_device(self.t_staff_deny)
        denied_op = str(uuid.uuid4())
        before = self.db.query(main.Product).filter(main.Product.business_id == self.biz_a.id).count()
        denied = self._replay_product_create(self.t_staff_deny, denied_device, self.staff_deny_a, op_id=denied_op)
        self.assertEqual(denied.status_code, 403, denied.text)
        # require_permission() raises with a plain-string detail; the offline
        # layer's own failures use {code, message}. The app's sync accepts both
        # (a 403 without a code is treated as PERMISSION_CHANGED), so assert on
        # the text wherever it lives rather than on one shape.
        detail = denied.json()["detail"]
        message = detail if isinstance(detail, str) else detail.get("message", "")
        self.assertIn("Add Products", message)
        # The replay route claims idempotency BEFORE the permission check (the
        # opposite order to POST /products/). Prove the claim does not survive
        # the denial: no product and no offline_v2 claim for this op, so a
        # retry after the permission is restored is processed, not swallowed.
        self.db.expire_all()
        self.assertEqual(self.db.query(main.Product).filter(main.Product.business_id == self.biz_a.id).count(), before)
        self.assertIsNone(self.db.query(main.MutationIdempotency).filter(
            main.MutationIdempotency.business_id == self.biz_a.id,
            main.MutationIdempotency.client_ref == denied_op,
        ).first())

    def test_changing_the_permission_invalidates_an_existing_offline_grant(self):
        device_id = self._provision_device(self.t_staff)
        self.staff_a.permission_overrides = json.dumps({"inventory.add_product": False})
        self.db.commit(); self.db.expire_all()
        r = self._replay_product_create(self.t_staff, device_id, self.staff_a)
        self.assertEqual(r.status_code, 403, r.text)
        self.assertEqual(r.json()["detail"]["code"], "PERMISSION_CHANGED")

    # ------------------------------------------------------ tenant isolation
    def test_tenant_b_data_is_never_reachable_from_tenant_a(self):
        suppliers = self.get("/suppliers/", self.t_staff_grant).json()
        self.assertNotIn("Tenant B Supplier", {s["name"] for s in suppliers})
        warehouses = self.get("/warehouses/", self.t_staff_grant).json()
        self.assertNotIn("Tenant B Warehouse", {w["name"] for w in warehouses})
        expenses = self.get("/expenses/", self.t_staff_grant).json()
        self.assertNotIn("tenant b", {row["note"] for row in expenses["expenses"]})
        b_expenses = self.get("/expenses/", self.t_admin_b).json()
        self.assertEqual(b_expenses["total"], 1)

    def test_tenant_b_admin_cannot_use_tenant_a_warehouse_identity(self):
        rows = self.get("/warehouses/operational", self.t_admin_b).json()
        self.assertEqual({row["name"] for row in rows}, {"Tenant B Warehouse"})

    # ------------------------------------------- regression: enforced already
    def test_previously_enforced_staff_denials_still_hold(self):
        product = self.client.post("/products/", json=self.product_payload(), headers=self.auth(self.t_admin))
        self.assertEqual(product.status_code, 200, product.text)
        product_id = product.json()["id"]
        checks = [
            ("patch", f"/products/{product_id}", {"name": "Renamed"}),
            ("delete", f"/products/{product_id}", None),
            ("get", "/sales/history", None),
            ("get", "/financial-summary", None),
            ("get", "/audit-logs", None),
            ("get", "/purchase-orders/", None),
        ]
        for method, path, payload in checks:
            with self.subTest(path=path):
                call = getattr(self.client, method)
                r = call(path, json=payload, headers=self.auth(self.t_staff)) if payload is not None \
                    else call(path, headers=self.auth(self.t_staff))
                self.assertEqual(r.status_code, 403, f"{path}: {r.text}")


if __name__ == "__main__":
    unittest.main()
