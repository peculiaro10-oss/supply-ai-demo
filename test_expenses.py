"""Automated tests for the Expenses feature (Cauldra).

Runs against a real, isolated PostgreSQL schema shared for the whole module
(see tests/postgres_test_support.py — the same create-schema/alembic-upgrade/
drop-schema pattern tests/test_mutation_idempotency_postgres.py already
uses). Each test's own setUp() creates brand-new, uniquely-named businesses/
users, so tests never interfere with each other despite sharing one schema.
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
    _ctx = create_postgres_test_schema("cauldra_expenses")
    main = _ctx.main


def tearDownModule():
    if _ctx is not None:
        drop_postgres_test_schema(_ctx, "cauldra_expenses")


@unittest.skipUnless(ADMIN_URL, "TEST_POSTGRES_ADMIN_URL is not configured")
class ExpenseTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient

        suffix = uuid.uuid4().hex[:10]
        db = main.SessionLocal()
        biz_a = main.BusinessProfile(business_code=f"EXP-A-{suffix}", company_name="Business A")
        biz_b = main.BusinessProfile(business_code=f"EXP-B-{suffix}", company_name="Business B")
        db.add_all([biz_a, biz_b])
        db.flush()

        admin_a = main.User(username=f"Admin A {suffix}", password=main.hash_password("AdminPass9"), role="admin",
                             email=f"admina-{suffix}@test.com", phone="1", business_id=biz_a.id, disabled=False)
        staff_a = main.User(username=f"Staff A {suffix}", password=main.hash_password("StaffPass9"), role="staff",
                             email=f"staffa-{suffix}@test.com", phone="2", business_id=biz_a.id, disabled=False)
        admin_b = main.User(username=f"Admin B {suffix}", password=main.hash_password("AdminPass9"), role="admin",
                             email=f"adminb-{suffix}@test.com", phone="3", business_id=biz_b.id, disabled=False)
        db.add_all([admin_a, staff_a, admin_b])
        db.commit()

        client = TestClient(main.app)

        def login(business_code, username, password):
            r = client.post("/auth/admin-login", json={"business_id": business_code, "username": username, "password": password})
            assert r.status_code == 200, r.text
            return r.json()["access_token"]

        self.main = main
        self.db = db
        self.client = client
        self.biz_a = biz_a
        self.biz_b = biz_b
        self.admin_a = admin_a
        self.staff_a = staff_a
        self.admin_b = admin_b
        self.token_admin_a = login(biz_a.business_code, admin_a.username, "AdminPass9")
        self.token_admin_b = login(biz_b.business_code, admin_b.username, "AdminPass9")

    def tearDown(self):
        self.db.close()

    def auth(self, token):
        return {"Authorization": f"Bearer {token}"}

    def login_employee(self, business_code, username, password, role):
        r = self.client.post("/auth/employee-login", json={"business_id": business_code, "username": username, "password": password, "selected_role": role})
        assert r.status_code == 200, r.text
        return r.json()["access_token"]

    def test_create_expense_success(self):
        r = self.client.post("/expenses/", json={"category": "Electricity", "amount": 50000, "payment_source": "Business Account"}, headers=self.auth(self.token_admin_a))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("id", r.json())

    def test_invalid_amount_rejected(self):
        r = self.client.post("/expenses/", json={"category": "Fuel", "amount": 0}, headers=self.auth(self.token_admin_a))
        self.assertEqual(r.status_code, 400, r.text)
        r2 = self.client.post("/expenses/", json={"category": "Fuel", "amount": -500}, headers=self.auth(self.token_admin_a))
        self.assertEqual(r2.status_code, 400, r2.text)

    def test_missing_category_rejected(self):
        r = self.client.post("/expenses/", json={"category": "   ", "amount": 1000}, headers=self.auth(self.token_admin_a))
        self.assertEqual(r.status_code, 400, r.text)

    def test_custom_category_accepted(self):
        r = self.client.post("/expenses/", json={"category": "Christmas Decorations", "amount": 5000}, headers=self.auth(self.token_admin_a))
        self.assertEqual(r.status_code, 200, r.text)
        listing = self.client.get("/expenses/", headers=self.auth(self.token_admin_a)).json()
        self.assertTrue(any(e["category"] == "Christmas Decorations" for e in listing["expenses"]))

    def test_categories_endpoint_returns_curated_list(self):
        r = self.client.get("/expenses/categories", headers=self.auth(self.token_admin_a))
        self.assertEqual(r.status_code, 200, r.text)
        cats = r.json()["categories"]
        self.assertIn("Electricity", cats)
        self.assertIn("Rent", cats)
        self.assertGreater(len(cats), 20)

    def test_automatic_user_and_timestamp_attribution(self):
        r = self.client.post("/expenses/", json={"category": "Fuel", "amount": 3000}, headers=self.auth(self.token_admin_a))
        self.assertEqual(r.status_code, 200, r.text)
        listing = self.client.get("/expenses/", headers=self.auth(self.token_admin_a)).json()
        row = listing["expenses"][0]
        self.assertEqual(row["recorded_by"], self.admin_a.username, row)
        self.assertTrue(row["created_at"], "created_at must be auto-populated")

    def test_frontend_cannot_spoof_creator_or_business(self):
        """The request schema has no owner_id/business_id field at all, so
        attempting to supply one must be silently ignored, never honored."""
        r = self.client.post("/expenses/", json={"category": "Fuel", "amount": 3000, "owner_id": 999999, "business_id": 999999}, headers=self.auth(self.token_admin_a))
        self.assertEqual(r.status_code, 200, r.text)
        listing = self.client.get("/expenses/", headers=self.auth(self.token_admin_a)).json()
        row = listing["expenses"][0]
        self.assertNotEqual(row["owner_id"], 999999)
        self.assertEqual(row["recorded_by"], self.admin_a.username)

    def test_tenant_isolation(self):
        self.client.post("/expenses/", json={"category": "Rent", "amount": 100000}, headers=self.auth(self.token_admin_a))
        self.client.post("/expenses/", json={"category": "Rent", "amount": 200000}, headers=self.auth(self.token_admin_b))

        a_listing = self.client.get("/expenses/", headers=self.auth(self.token_admin_a)).json()
        b_listing = self.client.get("/expenses/", headers=self.auth(self.token_admin_b)).json()
        self.assertEqual(a_listing["total"], 1, a_listing)
        self.assertEqual(b_listing["total"], 1, b_listing)
        self.assertEqual(a_listing["expenses"][0]["amount"], 100000)
        self.assertEqual(b_listing["expenses"][0]["amount"], 200000)

    def test_unauthorized_access_rejected(self):
        r = self.client.post("/expenses/", json={"category": "Fuel", "amount": 1000})
        self.assertEqual(r.status_code, 401, r.text)
        r2 = self.client.get("/expenses/")
        self.assertEqual(r2.status_code, 401, r2.text)

    def test_staff_can_record_expense(self):
        """Matches the existing role convention used by /sales/checkout
        (admin, manager, and staff can all record everyday transactions)."""
        token_staff_a = self.login_employee(self.biz_a.business_code, self.staff_a.username, "StaffPass9", "staff")
        r = self.client.post("/expenses/", json={"category": "Fuel", "amount": 2000}, headers=self.auth(token_staff_a))
        self.assertEqual(r.status_code, 200, r.text)

    def test_history_search_and_category_filter(self):
        self.client.post("/expenses/", json={"category": "Electricity", "amount": 50000}, headers=self.auth(self.token_admin_a))
        self.client.post("/expenses/", json={"category": "Fuel", "amount": 12000}, headers=self.auth(self.token_admin_a))
        self.client.post("/expenses/", json={"category": "Transportation", "amount": 8000}, headers=self.auth(self.token_admin_a))

        by_category = self.client.get("/expenses/?category=Fuel", headers=self.auth(self.token_admin_a)).json()
        self.assertEqual(by_category["total"], 1)
        self.assertEqual(by_category["expenses"][0]["category"], "Fuel")

        by_search = self.client.get("/expenses/?search=elect", headers=self.auth(self.token_admin_a)).json()
        self.assertEqual(by_search["total"], 1)
        self.assertEqual(by_search["expenses"][0]["category"], "Electricity")

    def test_history_date_range_filter(self):
        from datetime import datetime, timedelta

        db2 = main.SessionLocal()
        # Uses the real biz_a.id from setUp() rather than a hardcoded 1 —
        # correct regardless of how many other tests have already inserted
        # businesses into this shared schema.
        old = main.Expense(business_id=self.biz_a.id, category="Rent", amount=100000, owner_id=self.admin_a.id, created_at=datetime.utcnow() - timedelta(days=40))
        recent = main.Expense(business_id=self.biz_a.id, category="Rent", amount=100000, owner_id=self.admin_a.id, created_at=datetime.utcnow())
        db2.add_all([old, recent])
        db2.commit()
        db2.close()

        today = datetime.utcnow().strftime("%Y-%m-%d")
        result = self.client.get(f"/expenses/?date_from={today}", headers=self.auth(self.token_admin_a)).json()
        self.assertEqual(result["total"], 1, result)

    def test_payment_source_optional(self):
        r = self.client.post("/expenses/", json={"category": "Fuel", "amount": 1000}, headers=self.auth(self.token_admin_a))
        self.assertEqual(r.status_code, 200, r.text)
        listing = self.client.get("/expenses/", headers=self.auth(self.token_admin_a)).json()
        self.assertIsNone(listing["expenses"][0]["payment_source"])

    def test_idempotent_client_ref_prevents_duplicate(self):
        """Foundation for a future offline-sync client: retrying the same
        client_ref must not create a second row. No production caller sends
        client_ref today — this only proves the mechanism works when it's
        eventually used."""
        client_ref = f"local-abc-{uuid.uuid4().hex[:8]}"
        r1 = self.client.post("/expenses/", json={"category": "Fuel", "amount": 1000, "client_ref": client_ref}, headers=self.auth(self.token_admin_a))
        self.assertEqual(r1.status_code, 200, r1.text)
        r2 = self.client.post("/expenses/", json={"category": "Fuel", "amount": 1000, "client_ref": client_ref}, headers=self.auth(self.token_admin_a))
        self.assertEqual(r2.status_code, 200, r2.text)
        self.assertIs(r2.json().get("duplicate"), True)
        self.assertEqual(r1.json()["id"], r2.json()["id"])
        listing = self.client.get("/expenses/", headers=self.auth(self.token_admin_a)).json()
        self.assertEqual(listing["total"], 1, listing)

    def test_pagination(self):
        for i in range(5):
            self.client.post("/expenses/", json={"category": "Fuel", "amount": 100 + i}, headers=self.auth(self.token_admin_a))
        page1 = self.client.get("/expenses/?limit=2&offset=0", headers=self.auth(self.token_admin_a)).json()
        page2 = self.client.get("/expenses/?limit=2&offset=2", headers=self.auth(self.token_admin_a)).json()
        self.assertEqual(page1["total"], 5)
        self.assertEqual(len(page1["expenses"]), 2)
        self.assertEqual(len(page2["expenses"]), 2)
        self.assertNotEqual(page1["expenses"][0]["id"], page2["expenses"][0]["id"])


if __name__ == "__main__":
    unittest.main()
