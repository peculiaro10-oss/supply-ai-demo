"""Regression tests for the Staff role-permission update: Staff may now
create products and use the Add Product barcode workflow, but must remain
blocked from editing, deleting, and transferring stock.

PostgreSQL-backed (skipped unless TEST_POSTGRES_ADMIN_URL is configured —
see tests/postgres_test_support.py): one disposable schema for the whole
module, dropped in tearDownModule even on failure. No test here contacts
the real UPCitemdb API.
"""
from __future__ import annotations

import unittest
import uuid

from postgres_test_support import ADMIN_URL, create_postgres_test_schema, drop_postgres_test_schema

_ctx = None
main = None


def setUpModule():
    global _ctx, main
    if not ADMIN_URL:
        return
    _ctx = create_postgres_test_schema("cauldra_staffperm")
    main = _ctx.main


def tearDownModule():
    if _ctx is not None:
        drop_postgres_test_schema(_ctx, "cauldra_staffperm")


@unittest.skipUnless(ADMIN_URL, "TEST_POSTGRES_ADMIN_URL is not configured")
class StaffAddProductPermissionTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient

        suffix = uuid.uuid4().hex[:10]
        self.suffix = suffix
        db = main.SessionLocal()
        biz_a = main.BusinessProfile(business_code=f"SP-A-{suffix}", company_name="Staff Perm Test A")
        biz_b = main.BusinessProfile(business_code=f"SP-B-{suffix}", company_name="Staff Perm Test B")
        db.add_all([biz_a, biz_b])
        db.flush()
        # The real /auth/register-business flow seeds this automatically;
        # creating BusinessProfile directly via the ORM here bypasses that.
        db.add_all([
            main.Warehouse(business_id=biz_a.id, name="Main Central Warehouse", is_active=True),
            main.Warehouse(business_id=biz_a.id, name="Secondary Warehouse", is_active=True),
            main.Warehouse(business_id=biz_b.id, name="Other Business Warehouse", is_active=True),
        ])
        staff_a = main.User(username=f"Staff A {suffix}", password=main.hash_password("StaffPass9"), role="staff",
                             email=f"staffa-{suffix}@test.com", phone="1", business_id=biz_a.id, disabled=False)
        manager_a = main.User(username=f"Manager A {suffix}", password=main.hash_password("ManagerPass9"), role="manager",
                               email=f"managera-{suffix}@test.com", phone="2", business_id=biz_a.id, disabled=False)
        admin_a = main.User(username=f"Admin A {suffix}", password=main.hash_password("AdminPass9"), role="admin",
                             email=f"admina-{suffix}@test.com", phone="3", business_id=biz_a.id, disabled=False)
        db.add_all([staff_a, manager_a, admin_a])
        db.commit()

        client = TestClient(main.app)

        def login_employee(business_code, username, password, role):
            r = client.post("/auth/employee-login", json={"business_id": business_code, "username": username, "password": password, "selected_role": role})
            assert r.status_code == 200, r.text
            return r.json()["access_token"]

        def login_admin(business_code, username, password):
            r = client.post("/auth/admin-login", json={"business_id": business_code, "username": username, "password": password})
            assert r.status_code == 200, r.text
            return r.json()["access_token"]

        self.db = db
        self.client = client
        self.biz_a, self.biz_b = biz_a, biz_b
        self.staff_a, self.manager_a, self.admin_a = staff_a, manager_a, admin_a
        self.token_staff_a = login_employee(biz_a.business_code, staff_a.username, "StaffPass9", "staff")
        self.token_manager_a = login_employee(biz_a.business_code, manager_a.username, "ManagerPass9", "manager")
        self.token_admin_a = login_admin(biz_a.business_code, admin_a.username, "AdminPass9")

    def tearDown(self):
        self.db.close()

    def auth(self, token):
        return {"Authorization": f"Bearer {token}"}

    def product_payload(self, **overrides):
        payload = {
            "name": "Staff Test Widget", "category": "General", "size": "1 unit",
            "warehouse": "Main Central Warehouse", "quantity": 10, "min_stock_level": 2,
            "cost_price": 5.0, "wholesale_price": 8.0, "retail_price": 12.0,
        }
        payload.update(overrides)
        return payload

    # --- Staff CAN create products (item 1) -----------------------------

    def test_staff_can_create_a_product(self):
        r = self.client.post("/products/", json=self.product_payload(name="Staff Created Widget"), headers=self.auth(self.token_staff_a))
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertIn("id", body)
        self.assertEqual(body["name"], "Staff Created Widget")

    def test_staff_created_product_is_scoped_to_their_own_business_and_attributed_to_them(self):
        r = self.client.post("/products/", json=self.product_payload(name="Attribution Check"), headers=self.auth(self.token_staff_a))
        self.assertEqual(r.status_code, 200, r.text)
        product_id = r.json()["id"]
        product = self.db.query(main.Product).filter(main.Product.id == product_id).first()
        self.assertEqual(product.business_id, self.biz_a.id)
        self.assertEqual(product.owner_id, self.staff_a.id)
        # ProductCreate has no business_id field at all -- structurally
        # impossible for a client payload to set it; confirmed here too.
        self.assertNotIn("business_id", main.ProductCreate.model_fields)
        audit = self.db.query(main.AuditLog).filter(
            main.AuditLog.business_id == self.biz_a.id, main.AuditLog.action == "PRODUCT_CREATED",
        ).order_by(main.AuditLog.id.desc()).first()
        self.assertIsNotNone(audit)
        self.assertEqual(audit.actor_username, self.staff_a.username)

    def test_staff_cannot_reference_a_warehouse_belonging_to_another_business(self):
        r = self.client.post("/products/", json=self.product_payload(name="Cross-Business Warehouse", warehouse="Other Business Warehouse"), headers=self.auth(self.token_staff_a))
        self.assertEqual(r.status_code, 400, r.text)

    def test_staff_creation_still_enforces_duplicate_sku_rule(self):
        r1 = self.client.post("/products/", json=self.product_payload(name="Dup SKU One", sku="STAFF-SKU-1"), headers=self.auth(self.token_staff_a))
        self.assertEqual(r1.status_code, 200, r1.text)
        r2 = self.client.post("/products/", json=self.product_payload(name="Dup SKU Two", sku="STAFF-SKU-1"), headers=self.auth(self.token_staff_a))
        self.assertEqual(r2.status_code, 409, r2.text)

    def test_staff_creation_still_enforces_duplicate_barcode_rule(self):
        barcode = f"{9000000000000 + int(self.suffix[:6], 16) % 900000}"[:13]
        r1 = self.client.post("/products/", json=self.product_payload(name="Barcode One", barcode=barcode), headers=self.auth(self.token_staff_a))
        self.assertEqual(r1.status_code, 200, r1.text)
        r2 = self.client.post("/products/", json=self.product_payload(name="Barcode Two Same Code", barcode=barcode), headers=self.auth(self.token_staff_a))
        self.assertEqual(r2.status_code, 409, r2.text)

    def test_staff_offline_replay_client_ref_still_deduplicates(self):
        client_ref = f"staff-replay-{self.suffix}"
        r1 = self.client.post("/products/", json=self.product_payload(name="Replay Item", client_ref=client_ref), headers=self.auth(self.token_staff_a))
        self.assertEqual(r1.status_code, 200, r1.text)
        r2 = self.client.post("/products/", json=self.product_payload(name="Replay Item", client_ref=client_ref), headers=self.auth(self.token_staff_a))
        self.assertEqual(r2.status_code, 200, r2.text)
        self.assertEqual(r1.json()["id"], r2.json()["id"])

    # --- Staff CAN use the Add Product barcode workflow (item 2) --------

    def test_staff_can_use_barcode_lookup(self):
        barcode = f"{8000000000000 + int(self.suffix[:6], 16) % 900000}"[:13]
        r = self.client.post("/catalog/barcode-lookup", json={"barcode": barcode}, headers=self.auth(self.token_staff_a))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn(r.json()["source"], {"not_found", "cauldra_catalog", "upcitemdb", "upcitemdb_unavailable", "catalog_error"})

    def test_staff_can_use_the_live_duplicate_check_hint(self):
        r = self.client.post("/products/duplicate-check", json={"name": "Staff Test Widget"}, headers=self.auth(self.token_staff_a))
        self.assertEqual(r.status_code, 200, r.text)

    def test_staff_can_create_a_product_from_barcode_assisted_information(self):
        barcode = f"{7000000000000 + int(self.suffix[:6], 16) % 900000}"[:13]
        lookup = self.client.post("/catalog/barcode-lookup", json={"barcode": barcode}, headers=self.auth(self.token_staff_a))
        self.assertEqual(lookup.status_code, 200, lookup.text)
        create = self.client.post("/products/", json=self.product_payload(name="Barcode Assisted Item", barcode=barcode), headers=self.auth(self.token_staff_a))
        self.assertEqual(create.status_code, 200, create.text)

    # --- Staff restrictions that MUST remain intact (item 3) ------------

    def test_staff_still_cannot_edit_an_existing_product(self):
        created = self.client.post("/products/", json=self.product_payload(name="Not Editable By Staff"), headers=self.auth(self.token_staff_a))
        product_id = created.json()["id"]
        r = self.client.patch(f"/products/{product_id}", json={"name": "Renamed"}, headers=self.auth(self.token_staff_a))
        self.assertEqual(r.status_code, 403, r.text)

    def test_staff_still_cannot_delete_a_product(self):
        created = self.client.post("/products/", json=self.product_payload(name="Not Deletable By Staff"), headers=self.auth(self.token_staff_a))
        product_id = created.json()["id"]
        r = self.client.delete(f"/products/{product_id}", headers=self.auth(self.token_staff_a))
        self.assertEqual(r.status_code, 403, r.text)

    def test_staff_still_cannot_transfer_stock(self):
        created = self.client.post("/products/", json=self.product_payload(name="Not Transferable By Staff"), headers=self.auth(self.token_staff_a))
        product_id = created.json()["id"]
        r = self.client.patch(
            f"/products/{product_id}/transfer",
            json={"from_warehouse": "Main Central Warehouse", "to_warehouse": "Secondary Warehouse", "quantity": 1},
            headers=self.auth(self.token_staff_a),
        )
        self.assertEqual(r.status_code, 403, r.text)

    # --- Manager/Admin unaffected (item 7) -------------------------------

    def test_manager_can_still_create_edit_and_delete_products(self):
        created = self.client.post("/products/", json=self.product_payload(name="Manager Owned Item"), headers=self.auth(self.token_manager_a))
        self.assertEqual(created.status_code, 200, created.text)
        product_id = created.json()["id"]
        edited = self.client.patch(f"/products/{product_id}", json={"name": "Manager Renamed"}, headers=self.auth(self.token_manager_a))
        self.assertEqual(edited.status_code, 200, edited.text)
        # Manager delete routes through the admin-approval request flow (unchanged).
        deleted = self.client.delete(f"/products/{product_id}", headers=self.auth(self.token_manager_a))
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertIn("approval", deleted.json().get("message", "").lower())

    def test_admin_can_still_create_edit_and_delete_products(self):
        created = self.client.post("/products/", json=self.product_payload(name="Admin Owned Item"), headers=self.auth(self.token_admin_a))
        self.assertEqual(created.status_code, 200, created.text)
        product_id = created.json()["id"]
        edited = self.client.patch(f"/products/{product_id}", json={"name": "Admin Renamed"}, headers=self.auth(self.token_admin_a))
        self.assertEqual(edited.status_code, 200, edited.text)
        deleted = self.client.delete(f"/products/{product_id}", headers=self.auth(self.token_admin_a))
        self.assertEqual(deleted.status_code, 200, deleted.text)


if __name__ == "__main__":
    unittest.main()
