"""Automated tests for the Business Day lifecycle (Cauldra).

Runs against a real, isolated PostgreSQL schema (see
tests/postgres_test_support.py — the same create-schema/alembic-upgrade/
drop-schema pattern tests/test_mutation_idempotency_postgres.py already
uses). One schema is created for the whole module (setUpModule/tearDownModule
below) and shared by every TestCase class in this file — they all test
facets of the same Business Day feature, so isolation between them isn't
needed; isolation BETWEEN test methods is what actually matters, and that's
provided by each test's own setUp() creating brand-new, uniquely-named
businesses/users, so no two tests ever share a row.

Covers the acceptance criteria from the Business Day redesign: lifecycle
(start/close/reopen/close-again), permission enforcement per role, no
auto-open from read-only requests, immutability of historical closures,
reopen request/approve/reject workflow, direct-reopen, audit trail
completeness/ordering, additive corrections/adjustments, concurrency/
duplicate-day prevention, and continued correctness of existing sales/
expense/history endpoints.
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
    _ctx = create_postgres_test_schema("cauldra_bday")
    main = _ctx.main


def tearDownModule():
    if _ctx is not None:
        drop_postgres_test_schema(_ctx, "cauldra_bday")


@unittest.skipUnless(ADMIN_URL, "TEST_POSTGRES_ADMIN_URL is not configured")
class _BusinessDayTestBase(unittest.TestCase):
    """Shared setUp: two businesses (A with admin/manager/staff, B with just
    an admin), matching the original SETUP script exactly, but with a fresh
    unique business_code/email per test so many tests can safely share one
    PostgreSQL schema."""

    def setUp(self):
        from fastapi.testclient import TestClient

        suffix = uuid.uuid4().hex[:10]
        db = main.SessionLocal()
        biz_a = main.BusinessProfile(business_code=f"BD-A-{suffix}", company_name="Business A")
        biz_b = main.BusinessProfile(business_code=f"BD-B-{suffix}", company_name="Business B")
        db.add_all([biz_a, biz_b])
        db.flush()

        admin_a = main.User(username=f"Admin A {suffix}", password=main.hash_password("AdminPass9"), role="admin",
                             email=f"admina-{suffix}@test.com", phone="1", business_id=biz_a.id, disabled=False)
        manager_a = main.User(username=f"Manager A {suffix}", password=main.hash_password("ManagerPass9"), role="manager",
                               email=f"managera-{suffix}@test.com", phone="2", business_id=biz_a.id, disabled=False)
        staff_a = main.User(username=f"Staff A {suffix}", password=main.hash_password("StaffPass9"), role="staff",
                             email=f"staffa-{suffix}@test.com", phone="3", business_id=biz_a.id, disabled=False)
        admin_b = main.User(username=f"Admin B {suffix}", password=main.hash_password("AdminPass9"), role="admin",
                             email=f"adminb-{suffix}@test.com", phone="4", business_id=biz_b.id, disabled=False)
        db.add_all([admin_a, manager_a, staff_a, admin_b])
        db.commit()

        client = TestClient(main.app)

        def login_admin(business_code, username, password):
            r = client.post("/auth/admin-login", json={"business_id": business_code, "username": username, "password": password})
            assert r.status_code == 200, r.text
            return r.json()["access_token"]

        def login_employee(business_code, username, password, role):
            r = client.post("/auth/employee-login", json={"business_id": business_code, "username": username, "password": password, "selected_role": role})
            assert r.status_code == 200, r.text
            return r.json()["access_token"]

        self.main = main
        self.db = db
        self.client = client
        self.biz_a = biz_a
        self.biz_b = biz_b
        self.admin_a = admin_a
        self.manager_a = manager_a
        self.staff_a = staff_a
        self.admin_b = admin_b
        self.token_admin_a = login_admin(biz_a.business_code, admin_a.username, "AdminPass9")
        self.token_manager_a = login_employee(biz_a.business_code, manager_a.username, "ManagerPass9", "manager")
        self.token_staff_a = login_employee(biz_a.business_code, staff_a.username, "StaffPass9", "staff")
        self.token_admin_b = login_admin(biz_b.business_code, admin_b.username, "AdminPass9")

    def tearDown(self):
        self.db.close()

    def auth(self, token):
        return {"Authorization": f"Bearer {token}"}

    def make_product(self, business_id, price=1000.0, qty=50):
        p = main.Product(business_id=business_id, sku="SKU-1", name="Widget", category="General",
                          quantity=qty, retail_price=price, cost_price=500.0)
        self.db.add(p)
        self.db.commit()
        self.db.refresh(p)
        return p


class BusinessDayLifecycleTests(_BusinessDayTestBase):
    def test_admin_manager_staff_can_all_open_a_business_day(self):
        # Business Day open/close is available to every operational role —
        # Admin, Manager, and Staff (see the Business Day role-permission
        # model) — Staff are often the ones physically running the counter.
        # Only Reopen/Request-Reopen remain restricted (tested elsewhere).
        for token in (self.token_admin_a, self.token_manager_a, self.token_staff_a):
            r = self.client.post("/sales/start-business-day", headers=self.auth(token))
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()["business_day"]["status"], "OPEN")
            r2 = self.client.post("/sales/end-business-day", headers=self.auth(token))
            self.assertEqual(r2.status_code, 200, r2.text)

    def test_admin_manager_staff_can_all_close_a_business_day(self):
        for token in (self.token_admin_a, self.token_manager_a, self.token_staff_a):
            self.client.post("/sales/start-business-day", headers=self.auth(token))
            r = self.client.post("/sales/end-business-day", headers=self.auth(token))
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()["business_day"]["status"], "CLOSED")
            self.assertIn("closing_snapshot", r.json())

        # Staff can also close a day that someone ELSE opened. Needs a fresh
        # business/session, so run it as a second scenario in the same test
        # exactly like the original did.
        self.setUp()
        self.client.post("/sales/start-business-day", headers=self.auth(self.token_admin_a))
        r = self.client.post("/sales/end-business-day", headers=self.auth(self.token_staff_a))
        self.assertEqual(r.status_code, 200, r.text)

    def test_read_only_current_day_never_creates_a_day(self):
        for _ in range(3):
            r = self.client.get("/sales/current-day", headers=self.auth(self.token_admin_a))
            self.assertEqual(r.status_code, 200, r.text)
            self.assertIs(r.json()["open"], False)
            self.assertEqual(r.json()["status"], "NOT_STARTED")
            self.assertIsNone(r.json()["business_day"])

        listing = self.client.get("/sales/history", headers=self.auth(self.token_admin_a))
        self.assertEqual(listing.status_code, 200, listing.text)
        self.assertEqual(listing.json(), [])

        analytics = self.client.get("/sales/analytics", headers=self.auth(self.token_admin_a))
        self.assertEqual(analytics.status_code, 200, analytics.text)
        self.assertEqual(analytics.json()["days"], [])

    def test_sale_with_no_open_day_cleanly_starts_one(self):
        p = self.make_product(self.biz_a.id)
        before = self.client.get("/sales/current-day", headers=self.auth(self.token_staff_a)).json()
        self.assertIs(before["open"], False)

        r = self.client.post("/sales/checkout", json={"items": [{"product_id": p.id, "quantity": 2, "unit_price": p.retail_price}]}, headers=self.auth(self.token_staff_a))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIsNotNone(r.json()["business_day_id"])

        after = self.client.get("/sales/current-day", headers=self.auth(self.token_admin_a)).json()
        self.assertIs(after["open"], True)
        self.assertEqual(after["business_day"]["id"], r.json()["business_day_id"])
        self.assertEqual(after["business_day"]["transactions"], 1)

    def test_checkout_after_close_opens_a_brand_new_session(self):
        # A closed session from earlier today must never block new sales — a
        # closed Business Day is history, not a blocker (see the multi-
        # session Business Day model). Checkout auto-opens a fresh session
        # exactly like the very first sale of the day does.
        p = self.make_product(self.biz_a.id)
        r1 = self.client.post("/sales/checkout", json={"items": [{"product_id": p.id, "quantity": 1, "unit_price": p.retail_price}]}, headers=self.auth(self.token_staff_a))
        self.assertEqual(r1.status_code, 200, r1.text)
        first_day_id = r1.json()["business_day_id"]
        close_r = self.client.post("/sales/end-business-day", headers=self.auth(self.token_admin_a))
        self.assertEqual(close_r.status_code, 200, close_r.text)

        r2 = self.client.post("/sales/checkout", json={"items": [{"product_id": p.id, "quantity": 1, "unit_price": p.retail_price}]}, headers=self.auth(self.token_staff_a))
        self.assertEqual(r2.status_code, 200, r2.text)
        second_day_id = r2.json()["business_day_id"]
        self.assertNotEqual(second_day_id, first_day_id, "checkout after close must attach to a brand-new session, never reuse the closed one")

        rows = self.db.query(main.BusinessDay).filter(main.BusinessDay.business_id == self.biz_a.id).all()
        self.assertEqual(len(rows), 2, rows)

    def test_opening_while_another_session_active_is_blocked(self):
        # A business may have multiple sessions on the same calendar date,
        # but never two ACTIVE at once — the only thing that blocks Open is
        # another currently-active session, never "today already has a
        # closed/duplicate row" (see the multi-session Business Day model).
        r1 = self.client.post("/sales/start-business-day", headers=self.auth(self.token_admin_a))
        self.assertEqual(r1.status_code, 200, r1.text)
        id1 = r1.json()["business_day"]["id"]

        r2 = self.client.post("/sales/start-business-day", headers=self.auth(self.token_manager_a))
        self.assertEqual(r2.status_code, 409, r2.text)
        self.assertIn("another business day is currently open", r2.text.lower())

        rows = self.db.query(main.BusinessDay).filter(main.BusinessDay.business_id == self.biz_a.id).all()
        self.assertEqual(len(rows), 1, rows)

        close_r = self.client.post("/sales/end-business-day", headers=self.auth(self.token_admin_a))
        self.assertEqual(close_r.status_code, 200, close_r.text)

        r3 = self.client.post("/sales/start-business-day", headers=self.auth(self.token_manager_a))
        self.assertEqual(r3.status_code, 200, r3.text)
        self.assertNotEqual(r3.json()["business_day"]["id"], id1, "opening after a close must create a brand-new session, never reuse the closed one")

        rows2 = self.db.query(main.BusinessDay).filter(main.BusinessDay.business_id == self.biz_a.id).all()
        self.assertEqual(len(rows2), 2, rows2)

    def test_opening_after_close_creates_a_new_session_same_date(self):
        r1 = self.client.post("/sales/start-business-day", headers=self.auth(self.token_admin_a))
        id1 = r1.json()["business_day"]["id"]
        self.client.post("/sales/end-business-day", headers=self.auth(self.token_admin_a))
        r = self.client.post("/sales/start-business-day", headers=self.auth(self.token_admin_a))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertNotEqual(r.json()["business_day"]["id"], id1)
        self.assertEqual(r.json()["business_day"]["status"], "OPEN")
        self.assertEqual(r.json()["business_day"]["date"], r1.json()["business_day"]["date"])


class BusinessDayReopenWorkflowTests(_BusinessDayTestBase):
    def _open_and_close(self):
        self.client.post("/sales/start-business-day", headers=self.auth(self.token_admin_a))
        close_r = self.client.post("/sales/end-business-day", headers=self.auth(self.token_admin_a))
        return close_r.json()["business_day"]["id"]

    def test_staff_cannot_request_reopen(self):
        day_id = self._open_and_close()
        r = self.client.post(f"/business-days/{day_id}/reopen-request", json={"reason": "need to fix a sale"}, headers=self.auth(self.token_staff_a))
        self.assertEqual(r.status_code, 403, r.text)

    def test_manager_cannot_directly_reopen(self):
        day_id = self._open_and_close()
        r = self.client.post(f"/business-days/{day_id}/direct-reopen", json={"reason": "urgent fix"}, headers=self.auth(self.token_manager_a))
        self.assertEqual(r.status_code, 403, r.text)

    def test_staff_cannot_directly_reopen(self):
        day_id = self._open_and_close()
        r = self.client.post(f"/business-days/{day_id}/direct-reopen", json={"reason": "urgent fix"}, headers=self.auth(self.token_staff_a))
        self.assertEqual(r.status_code, 403, r.text)

    def test_manager_reopen_request_requires_reason(self):
        day_id = self._open_and_close()
        r = self.client.post(f"/business-days/{day_id}/reopen-request", json={"reason": "   "}, headers=self.auth(self.token_manager_a))
        self.assertEqual(r.status_code, 400, r.text)

    def test_duplicate_pending_reopen_requests_rejected(self):
        day_id = self._open_and_close()
        r1 = self.client.post(f"/business-days/{day_id}/reopen-request", json={"reason": "need to add a missed sale"}, headers=self.auth(self.token_manager_a))
        self.assertEqual(r1.status_code, 200, r1.text)
        r2 = self.client.post(f"/business-days/{day_id}/reopen-request", json={"reason": "another reason"}, headers=self.auth(self.token_manager_a))
        self.assertEqual(r2.status_code, 409, r2.text)

    def test_manager_can_see_own_pending_request_but_not_others(self):
        day_id = self._open_and_close()
        req = self.client.post(f"/business-days/{day_id}/reopen-request", json={"reason": "need to add a missed sale"}, headers=self.auth(self.token_manager_a))
        request_id = req.json()["id"]

        own = self.client.get("/business-days/reopen-requests", headers=self.auth(self.token_manager_a)).json()
        self.assertTrue(any(p["id"] == request_id for p in own), own)

        staff_denied = self.client.get("/business-days/reopen-requests", headers=self.auth(self.token_staff_a))
        self.assertEqual(staff_denied.status_code, 403, staff_denied.text)

    def test_manager_only_sees_their_own_requests_not_other_managers(self):
        day_id = self._open_and_close()
        other_manager = main.User(username=f"Manager Two {uuid.uuid4().hex[:8]}", password=main.hash_password("ManagerPass9"), role="manager",
                                   email=f"managertwo-{uuid.uuid4().hex[:8]}@test.com", phone="9", business_id=self.biz_a.id, disabled=False)
        self.db.add(other_manager)
        self.db.commit()
        r = self.client.post("/auth/employee-login", json={"business_id": self.biz_a.business_code, "username": other_manager.username, "password": "ManagerPass9", "selected_role": "manager"})
        self.assertEqual(r.status_code, 200, r.text)
        token_manager_2 = r.json()["access_token"]

        req = self.client.post(f"/business-days/{day_id}/reopen-request", json={"reason": "need to add a missed sale"}, headers=self.auth(self.token_manager_a))
        request_id = req.json()["id"]

        other_view = self.client.get("/business-days/reopen-requests", headers=self.auth(token_manager_2)).json()
        self.assertFalse(any(p["id"] == request_id for p in other_view), other_view)

    def test_admin_approve_reopens_and_preserves_original_closure(self):
        day_id = self._open_and_close()
        req = self.client.post(f"/business-days/{day_id}/reopen-request", json={"reason": "need to add a missed sale"}, headers=self.auth(self.token_manager_a))
        self.assertEqual(req.status_code, 200, req.text)
        request_id = req.json()["id"]

        pending = self.client.get("/business-days/reopen-requests", headers=self.auth(self.token_admin_a)).json()
        self.assertTrue(any(p["id"] == request_id for p in pending))

        approve = self.client.post(f"/business-days/reopen-requests/{request_id}/approve", json={"note": "looks legitimate"}, headers=self.auth(self.token_admin_a))
        self.assertEqual(approve.status_code, 200, approve.text)
        self.assertEqual(approve.json()["business_day"]["status"], "REOPENED")
        self.assertEqual(approve.json()["business_day"]["reopen_count"], 1)

        still_pending = self.client.get("/business-days/reopen-requests", headers=self.auth(self.token_admin_a)).json()
        self.assertFalse(any(p["id"] == request_id for p in still_pending))

        history = self.client.get("/business-days/reopen-requests/history", headers=self.auth(self.token_admin_a)).json()
        self.assertTrue(any(h["id"] == request_id and h["status"] == "APPROVED" for h in history))

        timeline = self.client.get(f"/business-days/{day_id}/timeline", headers=self.auth(self.token_admin_a)).json()
        actions = [e["action"] for e in timeline["events"]]
        self.assertEqual(actions, [
            "BUSINESS_DAY_STARTED", "BUSINESS_DAY_CLOSED",
            "BUSINESS_DAY_REOPEN_REQUESTED", "BUSINESS_DAY_REOPEN_APPROVED", "BUSINESS_DAY_REOPENED",
        ], actions)
        # The original closure is never erased even though the day is now reopened.
        self.assertIn("BUSINESS_DAY_CLOSED", actions)

    def test_admin_reject_leaves_day_closed_and_is_audited(self):
        day_id = self._open_and_close()
        req = self.client.post(f"/business-days/{day_id}/reopen-request", json={"reason": "need to add a missed sale"}, headers=self.auth(self.token_manager_a))
        request_id = req.json()["id"]

        reject = self.client.post(f"/business-days/reopen-requests/{request_id}/reject", json={"note": "not sufficient justification"}, headers=self.auth(self.token_admin_a))
        self.assertEqual(reject.status_code, 200, reject.text)
        self.assertEqual(reject.json()["business_day"]["status"], "CLOSED")

        history = self.client.get("/business-days/reopen-requests/history", headers=self.auth(self.token_admin_a)).json()
        self.assertTrue(any(h["id"] == request_id and h["status"] == "REJECTED" and h["resolution_note"] == "not sufficient justification" for h in history))

        timeline = self.client.get(f"/business-days/{day_id}/timeline", headers=self.auth(self.token_admin_a)).json()
        actions = [e["action"] for e in timeline["events"]]
        self.assertIn("BUSINESS_DAY_REOPEN_REJECTED", actions)
        self.assertNotIn("BUSINESS_DAY_REOPENED", actions)

    def test_resolving_non_pending_request_returns_404(self):
        day_id = self._open_and_close()
        req = self.client.post(f"/business-days/{day_id}/reopen-request", json={"reason": "x"}, headers=self.auth(self.token_manager_a))
        request_id = req.json()["id"]
        self.client.post(f"/business-days/reopen-requests/{request_id}/approve", headers=self.auth(self.token_admin_a))
        again = self.client.post(f"/business-days/reopen-requests/{request_id}/approve", headers=self.auth(self.token_admin_a))
        self.assertEqual(again.status_code, 404, again.text)

    def test_direct_reopen_is_admin_only_and_requires_reason(self):
        day_id = self._open_and_close()
        no_reason = self.client.post(f"/business-days/{day_id}/direct-reopen", json={"reason": ""}, headers=self.auth(self.token_admin_a))
        self.assertEqual(no_reason.status_code, 400, no_reason.text)

        ok = self.client.post(f"/business-days/{day_id}/direct-reopen", json={"reason": "admin override to fix a data-entry mistake"}, headers=self.auth(self.token_admin_a))
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertEqual(ok.json()["business_day"]["status"], "REOPENED")

        timeline = self.client.get(f"/business-days/{day_id}/timeline", headers=self.auth(self.token_admin_a)).json()
        actions = [e["action"] for e in timeline["events"]]
        self.assertEqual(actions, ["BUSINESS_DAY_STARTED", "BUSINESS_DAY_CLOSED", "BUSINESS_DAY_REOPENED"])
        last_event = timeline["events"][-1]
        self.assertIs(last_event["metadata"]["direct"], True)

    def test_reopen_then_close_again_preserves_both_closures(self):
        day_id = self._open_and_close()
        self.client.post(f"/business-days/{day_id}/direct-reopen", json={"reason": "reopening to add a sale"}, headers=self.auth(self.token_admin_a))
        reclose = self.client.post("/sales/end-business-day", headers=self.auth(self.token_admin_a))
        self.assertEqual(reclose.status_code, 200, reclose.text)

        timeline = self.client.get(f"/business-days/{day_id}/timeline", headers=self.auth(self.token_admin_a)).json()
        actions = [e["action"] for e in timeline["events"]]
        self.assertEqual(actions, ["BUSINESS_DAY_STARTED", "BUSINESS_DAY_CLOSED", "BUSINESS_DAY_REOPENED", "BUSINESS_DAY_CLOSED_AGAIN"])

    def test_reopen_request_rejected_when_day_not_closed(self):
        self.client.post("/sales/start-business-day", headers=self.auth(self.token_admin_a))
        day = self.client.get("/sales/current-day", headers=self.auth(self.token_admin_a)).json()["business_day"]
        r = self.client.post(f"/business-days/{day['id']}/reopen-request", json={"reason": "x"}, headers=self.auth(self.token_manager_a))
        self.assertEqual(r.status_code, 409, r.text)


class BusinessDayAdjustmentTests(_BusinessDayTestBase):
    def test_non_admin_cannot_record_sale_adjustment(self):
        p = self.make_product(self.biz_a.id)
        self.client.post("/sales/checkout", json={"items": [{"product_id": p.id, "quantity": 1, "unit_price": p.retail_price}]}, headers=self.auth(self.token_staff_a))
        sale_row = self.db.query(main.SaleModel).filter(main.SaleModel.business_id == self.biz_a.id).first()
        r = self.client.post(f"/sales/{sale_row.id}/adjustments", json={"reason": "wrong price", "amount_delta": -100}, headers=self.auth(self.token_manager_a))
        self.assertEqual(r.status_code, 403, r.text)
        r2 = self.client.post(f"/sales/{sale_row.id}/adjustments", json={"reason": "wrong price", "amount_delta": -100}, headers=self.auth(self.token_staff_a))
        self.assertEqual(r2.status_code, 403, r2.text)

    def test_admin_adjustment_is_additive_and_never_mutates_original(self):
        p = self.make_product(self.biz_a.id, price=1000.0)
        self.client.post("/sales/checkout", json={"items": [{"product_id": p.id, "quantity": 2, "unit_price": p.retail_price}]}, headers=self.auth(self.token_staff_a))
        sale_row = self.db.query(main.SaleModel).filter(main.SaleModel.business_id == self.biz_a.id).first()
        original_total = sale_row.total_price
        self.client.post("/sales/end-business-day", headers=self.auth(self.token_admin_a))

        # Reopen first (closed days block corrections) then adjust.
        self.client.post(f"/business-days/{sale_row.business_day_id}/direct-reopen", json={"reason": "fix a pricing mistake"}, headers=self.auth(self.token_admin_a))
        r = self.client.post(f"/sales/{sale_row.id}/adjustments", json={"reason": "price was recorded wrong", "amount_delta": -200}, headers=self.auth(self.token_admin_a))
        self.assertEqual(r.status_code, 200, r.text)

        self.db.refresh(sale_row)
        self.assertEqual(sale_row.total_price, original_total, "the original Sale row must never be mutated")

        listing = self.client.get(f"/sales/{sale_row.id}/adjustments", headers=self.auth(self.token_admin_a)).json()
        self.assertEqual(listing["original"]["total_price"], original_total)
        self.assertEqual(listing["final_total"], original_total - 200)
        self.assertEqual(len(listing["adjustments"]), 1)
        self.assertEqual(listing["adjustments"][0]["created_by_role"], "admin")

    def test_adjustment_blocked_while_day_closed_and_not_reopened(self):
        p = self.make_product(self.biz_a.id)
        self.client.post("/sales/checkout", json={"items": [{"product_id": p.id, "quantity": 1, "unit_price": p.retail_price}]}, headers=self.auth(self.token_staff_a))
        sale_row = self.db.query(main.SaleModel).filter(main.SaleModel.business_id == self.biz_a.id).first()
        self.client.post("/sales/end-business-day", headers=self.auth(self.token_admin_a))

        r = self.client.post(f"/sales/{sale_row.id}/adjustments", json={"reason": "late fix", "amount_delta": -50}, headers=self.auth(self.token_admin_a))
        self.assertEqual(r.status_code, 409, r.text)

    def test_expense_adjustment_additive(self):
        exp = self.client.post("/expenses/", json={"category": "Fuel", "amount": 5000}, headers=self.auth(self.token_admin_a))
        self.assertEqual(exp.status_code, 200, exp.text)
        expense_id = exp.json()["id"]

        r = self.client.post(f"/expenses/{expense_id}/adjustments", json={"reason": "receipt correction", "amount_delta": 500}, headers=self.auth(self.token_admin_a))
        self.assertEqual(r.status_code, 200, r.text)

        listing = self.client.get(f"/expenses/{expense_id}/adjustments", headers=self.auth(self.token_admin_a)).json()
        self.assertEqual(listing["original"]["amount"], 5000)
        self.assertEqual(listing["final_total"], 5500)

        expense_row = self.db.query(main.Expense).filter(main.Expense.id == expense_id).first()
        self.assertEqual(expense_row.amount, 5000, "the original Expense row must never be mutated")


class BusinessDayAuditAndIsolationTests(_BusinessDayTestBase):
    def test_full_audit_trail_has_who_what_when_and_metadata(self):
        start = self.client.post("/sales/start-business-day", headers=self.auth(self.token_admin_a))
        day_id = start.json()["business_day"]["id"]
        self.client.post("/sales/end-business-day", headers=self.auth(self.token_admin_a))

        timeline = self.client.get(f"/business-days/{day_id}/timeline", headers=self.auth(self.token_admin_a)).json()
        started_event = timeline["events"][0]
        self.assertEqual(started_event["actor_username"], self.admin_a.username)
        self.assertEqual(started_event["actor_role"], "admin")
        self.assertTrue(started_event["created_at"])
        self.assertEqual(started_event["metadata"]["role"], "admin")

        closed_event = timeline["events"][1]
        self.assertEqual(closed_event["action"], "BUSINESS_DAY_CLOSED")
        self.assertEqual(closed_event["metadata"]["sales_total"], 0)
        self.assertIn("transactions", closed_event["metadata"])

    def test_staff_cannot_view_timeline(self):
        start = self.client.post("/sales/start-business-day", headers=self.auth(self.token_admin_a))
        day_id = start.json()["business_day"]["id"]
        r = self.client.get(f"/business-days/{day_id}/timeline", headers=self.auth(self.token_staff_a))
        self.assertEqual(r.status_code, 403, r.text)

    def test_business_day_isolated_between_tenants(self):
        self.client.post("/sales/start-business-day", headers=self.auth(self.token_admin_a))
        r_b = self.client.get("/sales/current-day", headers=self.auth(self.token_admin_b))
        self.assertIs(r_b.json()["open"], False)

        start_a = self.client.get("/sales/current-day", headers=self.auth(self.token_admin_a)).json()["business_day"]
        cross_tenant = self.client.get(f"/business-days/{start_a['id']}/timeline", headers=self.auth(self.token_admin_b))
        self.assertEqual(cross_tenant.status_code, 404, cross_tenant.text)

    def test_unauthorized_access_rejected(self):
        r = self.client.post("/sales/start-business-day")
        self.assertEqual(r.status_code, 401, r.text)
        r2 = self.client.get("/sales/current-day")
        self.assertEqual(r2.status_code, 401, r2.text)


class ExistingFunctionalityRegressionTests(_BusinessDayTestBase):
    """Confirms sales/expense/history endpoints still behave correctly after
    the Business Day migration and rewrite — same shapes, correctly enriched,
    no regressions."""

    def test_sales_history_shape_and_totals(self):
        p = self.make_product(self.biz_a.id, price=2500.0)
        self.client.post("/sales/checkout", json={"items": [{"product_id": p.id, "quantity": 3, "unit_price": p.retail_price}]}, headers=self.auth(self.token_staff_a))
        self.client.post("/sales/end-business-day", headers=self.auth(self.token_admin_a))

        history = self.client.get("/sales/history", headers=self.auth(self.token_admin_a)).json()
        self.assertEqual(len(history), 1)
        row = history[0]
        self.assertEqual(row["transactions"], 1)
        self.assertEqual(row["items_sold"], 3)
        self.assertEqual(row["net_sales"], 7500.0)
        self.assertEqual(row["status"], "CLOSED")
        self.assertIsNotNone(row["opened_by_role"])
        self.assertEqual(row["closed_by_name"], self.admin_a.username)

    def test_sales_analytics_shape(self):
        p = self.make_product(self.biz_a.id, price=1000.0)
        self.client.post("/sales/checkout", json={"items": [{"product_id": p.id, "quantity": 1, "unit_price": p.retail_price}]}, headers=self.auth(self.token_staff_a))
        self.client.post("/sales/end-business-day", headers=self.auth(self.token_admin_a))

        analytics = self.client.get("/sales/analytics", headers=self.auth(self.token_admin_a)).json()
        self.assertEqual(len(analytics["days"]), 1)
        self.assertEqual(analytics["days"][0]["sales"], 1000.0)
        self.assertIn("average_daily_sales", analytics)
        self.assertIn("trend", analytics)

    def test_expense_creation_auto_opens_and_links_to_a_business_day(self):
        # Recording an expense is an operational financial action, so it OWNS
        # a Business Day exactly like completing a sale does: it attaches to
        # the active session, or auto-opens one when none is active. This
        # replaces the older rule (expenses attached only to an
        # already-open day and otherwise silently wrote business_day_id =
        # NULL, leaving the expense outside every business-day report it
        # belonged in). Read-only endpoints still never auto-open a day —
        # see test_read_only_current_day_never_creates_a_day.
        before = self.client.get("/sales/current-day", headers=self.auth(self.token_admin_a)).json()
        self.assertIs(before["open"], False)

        r = self.client.post("/expenses/", json={"category": "Fuel", "amount": 3000}, headers=self.auth(self.token_admin_a))
        self.assertEqual(r.status_code, 200, r.text)

        after = self.client.get("/sales/current-day", headers=self.auth(self.token_admin_a)).json()
        self.assertIs(after["open"], True, "recording an expense must auto-open a Business Day when none is active")
        first_expense = self.db.query(main.Expense).filter(main.Expense.business_id == self.biz_a.id, main.Expense.amount == 3000).first()
        self.assertEqual(first_expense.business_day_id, after["business_day"]["id"])

        autos = self.db.query(main.AuditLog).filter(main.AuditLog.business_id == self.biz_a.id, main.AuditLog.action == "BUSINESS_DAY_AUTO_OPENED").all()
        self.assertEqual(len(autos), 1, "the auto-open must be audited distinctly from a manual open")

        self.client.post("/sales/end-business-day", headers=self.auth(self.token_admin_a))
        self.client.post("/sales/start-business-day", headers=self.auth(self.token_admin_a))
        r2 = self.client.post("/expenses/", json={"category": "Fuel", "amount": 1500}, headers=self.auth(self.token_admin_a))
        self.assertEqual(r2.status_code, 200, r2.text)
        expense_row = self.db.query(main.Expense).filter(main.Expense.business_id == self.biz_a.id, main.Expense.amount == 1500).first()
        self.assertIsNotNone(expense_row.business_day_id)

    def test_checkout_idempotent_client_ref_still_works_with_business_day(self):
        p = self.make_product(self.biz_a.id)
        body = {"items": [{"product_id": p.id, "quantity": 1, "unit_price": p.retail_price}], "client_ref": f"local-xyz-{uuid.uuid4().hex[:8]}"}
        r1 = self.client.post("/sales/checkout", json=body, headers=self.auth(self.token_staff_a))
        self.assertEqual(r1.status_code, 200, r1.text)
        r2 = self.client.post("/sales/checkout", json=body, headers=self.auth(self.token_staff_a))
        self.assertEqual(r2.status_code, 200, r2.text)
        self.assertIs(r2.json().get("duplicate"), True)
        self.assertEqual(r1.json()["business_day_id"], r2.json()["business_day_id"])

        sales = self.db.query(main.SaleModel).filter(main.SaleModel.business_id == self.biz_a.id).all()
        self.assertEqual(len(sales), 1, "a retried client_ref must not create a second sale row")


class BusinessDayFinancialIntegrityTests(_BusinessDayTestBase):
    """Covers the Business Day financial-integrity hardening: explicit
    business_day_id ownership (not timestamp inference), immutable per-sale
    cost/price snapshots, /business-days/current-summary as the single
    source of truth behind the dashboard's "Sales Today"/"Net Profit
    (Current Business Day)" figures, and that a closed day's numbers can
    never move once historical."""

    def _checkout(self, product, qty, token=None, price_mode="retail"):
        body = {"items": [{"product_id": product.id, "quantity": qty, "price_mode": price_mode}]}
        r = self.client.post("/sales/checkout", json=body, headers=self.auth(token or self.token_staff_a))
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def test_new_business_day_zero_activity(self):
        self.client.post("/sales/start-business-day", headers=self.auth(self.token_admin_a))
        r = self.client.get("/business-days/current-summary", headers=self.auth(self.token_admin_a))
        d = r.json()
        self.assertIs(d["open"], True)
        self.assertEqual(d["sales"], 0.0)
        self.assertEqual(d["cogs"], 0.0)
        self.assertEqual(d["net_profit"], 0.0)
        self.assertEqual(d["transactions"], 0)

    def test_sale_receives_business_day_id_authoritatively(self):
        p = self.make_product(self.biz_a.id, price=1000.0)
        result = self._checkout(p, 2)
        day = self.db.query(main.BusinessDay).filter(main.BusinessDay.business_id == self.biz_a.id, main.BusinessDay.is_open == True).first()
        sale = self.db.query(main.SaleModel).filter(main.SaleModel.business_id == self.biz_a.id).first()
        self.assertEqual(sale.business_day_id, day.id)
        self.assertEqual(result["business_day_id"], day.id)

    def test_expense_receives_business_day_id_authoritatively(self):
        r = self.client.post("/expenses/", json={"category": "Rent", "amount": 5000}, headers=self.auth(self.token_admin_a))
        self.assertEqual(r.status_code, 200, r.text)
        day = self.db.query(main.BusinessDay).filter(main.BusinessDay.business_id == self.biz_a.id, main.BusinessDay.is_open == True).first()
        expense = self.db.query(main.Expense).filter(main.Expense.business_id == self.biz_a.id).first()
        self.assertEqual(expense.business_day_id, day.id)

    def test_sales_today_excludes_previous_business_day_same_calendar_date(self):
        # Two independent sessions on the same date must never be conflated
        # — the whole point of Business Day being a SESSION, not a date row.
        p = self.make_product(self.biz_a.id, price=1000.0)
        self._checkout(p, 1)  # Day A: NGN 1000
        self.client.post("/sales/end-business-day", headers=self.auth(self.token_admin_a))
        self.client.post("/sales/start-business-day", headers=self.auth(self.token_admin_a))
        r = self.client.get("/business-days/current-summary", headers=self.auth(self.token_admin_a))
        self.assertEqual(r.json()["sales"], 0.0, "Day B must start at zero even though Day A sold today")

        self._checkout(p, 1)  # Day B: NGN 1000, must not become 2000
        r2 = self.client.get("/business-days/current-summary", headers=self.auth(self.token_admin_a))
        self.assertEqual(r2.json()["sales"], 1000.0)

    def test_another_business_never_appears_in_current_summary(self):
        p_a = self.make_product(self.biz_a.id, price=1000.0)
        self._checkout(p_a, 1)
        r = self.client.get("/business-days/current-summary", headers=self.auth(self.token_admin_b))
        d = r.json()
        self.assertIs(d["open"], False, "Business B has no open day of its own and must never see Business A's")
        self.assertEqual(d["sales"], 0.0)

    def test_product_cost_price_change_does_not_alter_historical_cogs(self):
        p = self.make_product(self.biz_a.id, price=1000.0, qty=50)
        p.cost_price = 600.0
        self.db.commit()
        self._checkout(p, 2)
        before = self.client.get("/business-days/current-summary", headers=self.auth(self.token_admin_a)).json()
        self.assertEqual(before["cogs"], 1200.0)

        r = self.client.patch(f"/products/{p.id}", json={"cost_price": 900.0}, headers=self.auth(self.token_admin_a))
        self.assertEqual(r.status_code, 200, r.text)

        after = self.client.get("/business-days/current-summary", headers=self.auth(self.token_admin_a)).json()
        self.assertEqual(after["cogs"], 1200.0, "COGS must stay pinned to unit_cost_at_sale, never the product's current cost_price")

    def test_product_retail_price_change_does_not_alter_historical_revenue(self):
        p = self.make_product(self.biz_a.id, price=1000.0, qty=50)
        self._checkout(p, 2)
        before = self.client.get("/business-days/current-summary", headers=self.auth(self.token_admin_a)).json()
        self.assertEqual(before["sales"], 2000.0)

        r = self.client.patch(f"/products/{p.id}", json={"retail_price": 1500.0}, headers=self.auth(self.token_admin_a))
        self.assertEqual(r.status_code, 200, r.text)

        after = self.client.get("/business-days/current-summary", headers=self.auth(self.token_admin_a)).json()
        self.assertEqual(after["sales"], 2000.0, "revenue must stay pinned to the sale's own unit_price snapshot")

    def test_net_profit_equals_sales_minus_cogs_minus_expenses(self):
        p = self.make_product(self.biz_a.id, price=1000.0, qty=50)
        p.cost_price = 600.0
        self.db.commit()
        self._checkout(p, 2)  # sales=2000, cogs=1200
        self.client.post("/expenses/", json={"category": "Transport", "amount": 300}, headers=self.auth(self.token_admin_a))
        d = self.client.get("/business-days/current-summary", headers=self.auth(self.token_admin_a)).json()
        self.assertEqual(d["sales"], 2000.0)
        self.assertEqual(d["cogs"], 1200.0)
        self.assertEqual(d["expenses"], 300.0)
        self.assertEqual(d["net_profit"], 500.0)  # 2000 - 1200 - 300

    def test_expense_with_zero_sales_produces_negative_net_profit(self):
        r = self.client.post("/expenses/", json={"category": "Rent", "amount": 5000}, headers=self.auth(self.token_admin_a))
        self.assertEqual(r.status_code, 200, r.text)
        d = self.client.get("/business-days/current-summary", headers=self.auth(self.token_admin_a)).json()
        self.assertEqual(d["sales"], 0.0)
        self.assertEqual(d["net_profit"], -5000.0)

    def test_closing_business_day_preserves_its_financial_results(self):
        p = self.make_product(self.biz_a.id, price=1000.0, qty=50)
        p.cost_price = 600.0
        self.db.commit()
        self._checkout(p, 2)
        self.client.post("/expenses/", json={"category": "Transport", "amount": 300}, headers=self.auth(self.token_admin_a))
        live = self.client.get("/business-days/current-summary", headers=self.auth(self.token_admin_a)).json()

        r = self.client.post("/sales/end-business-day", headers=self.auth(self.token_admin_a))
        self.assertEqual(r.status_code, 200, r.text)
        snap = r.json()["closing_snapshot"]
        self.assertEqual(snap["sales"], live["sales"])
        self.assertEqual(snap["cogs"], live["cogs"])
        self.assertEqual(snap["net_profit"], live["net_profit"])

        # Changing product prices AFTER close must not move the closed
        # snapshot or the ledger it was built from.
        self.client.patch(f"/products/{p.id}", json={"cost_price": 900.0, "retail_price": 1500.0}, headers=self.auth(self.token_admin_a))
        day = self.db.query(main.BusinessDay).filter(main.BusinessDay.business_id == self.biz_a.id).order_by(main.BusinessDay.id.desc()).first()
        recomputed = main._business_day_financials(self.db, day)
        self.assertEqual(recomputed["sales"], live["sales"])
        self.assertEqual(recomputed["cogs"], live["cogs"])
        self.assertEqual(recomputed["net_profit"], live["net_profit"])

    def test_opening_next_business_day_starts_sales_today_at_zero_no_leak(self):
        p = self.make_product(self.biz_a.id, price=1000.0)
        self._checkout(p, 3)
        self.client.post("/sales/end-business-day", headers=self.auth(self.token_admin_a))

        r = self.client.post("/sales/start-business-day", headers=self.auth(self.token_admin_a))
        self.assertEqual(r.status_code, 200, r.text)
        d = self.client.get("/business-days/current-summary", headers=self.auth(self.token_admin_a)).json()
        self.assertEqual(d["sales"], 0.0)
        self.assertEqual(d["transactions"], 0)
        self.assertEqual(d["net_profit"], 0.0)

    def test_pricing_type_snapshot_recorded_and_immutable(self):
        p = self.make_product(self.biz_a.id, price=1000.0, qty=50)
        self._checkout(p, 1, price_mode="retail")
        sale = self.db.query(main.SaleModel).filter(main.SaleModel.business_id == self.biz_a.id).first()
        self.assertEqual(sale.pricing_type, "retail")
        self.assertEqual(sale.unit_price, 1000.0)
        original_cost = sale.unit_cost_at_sale

        self.client.patch(f"/products/{p.id}", json={"retail_price": 2000.0, "cost_price": 999.0}, headers=self.auth(self.token_admin_a))
        self.db.refresh(sale)
        self.assertEqual(sale.pricing_type, "retail")
        self.assertEqual(sale.unit_price, 1000.0)
        self.assertEqual(sale.unit_cost_at_sale, original_cost)

    def test_current_summary_and_sales_current_day_report_identical_sales(self):
        # Business Insights' "Sales Today" (current-summary) and the Sales
        # modal's own current-day tab (sales/current-day -> net_sales) must
        # be provably the same number, not two independent calculations.
        p = self.make_product(self.biz_a.id, price=1000.0, qty=50)
        self._checkout(p, 3)
        summary = self.client.get("/business-days/current-summary", headers=self.auth(self.token_admin_a)).json()
        current_day = self.client.get("/sales/current-day", headers=self.auth(self.token_admin_a)).json()
        self.assertEqual(summary["sales"], current_day["business_day"]["net_sales"])

    def test_malicious_checkout_with_other_business_product_id_rejected(self):
        p_b = self.make_product(self.biz_b.id, price=1000.0)
        r = self.client.post(
            "/sales/checkout",
            json={"items": [{"product_id": p_b.id, "quantity": 1, "price_mode": "retail"}]},
            headers=self.auth(self.token_staff_a),
        )
        self.assertEqual(r.status_code, 409, r.text)
        sales = self.db.query(main.SaleModel).filter(main.SaleModel.business_id == self.biz_a.id).all()
        self.assertEqual(len(sales), 0)


if __name__ == "__main__":
    unittest.main()
