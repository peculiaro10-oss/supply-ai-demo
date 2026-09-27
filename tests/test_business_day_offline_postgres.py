"""BUSINESS-DAY-OFFLINE-001: a Business Day opened on a device while offline,
through the real /offline/replay endpoint.

- a permitted user's offline day is created with the moment it was really
  opened, and the sale recorded against it then synchronizes;
- replaying the same change twice creates one day, not two;
- a user without business_day.manage is refused, exactly as online;
- another device already opened today's day at the location: the offline day
  is joined to it (no second open day) and its sale lands there;
- an open day from a DIFFERENT date is never joined: refused with a reason,
  nothing written;
- an unknown location is refused;
- closing offline: keeps the real close time, runs after the day's own work,
  an already-closed server day is answered unchanged, work by others after
  the offline close keeps the day open, permission rules as online;
- a sale opens a day with the sale permission, as online checkout does.

Set TEST_POSTGRES_ADMIN_URL (see tests/postgres_test_support.py).
"""
from __future__ import annotations

import hashlib
import json
import unittest
import uuid
from datetime import datetime, timedelta, timezone

from tests.postgres_test_support import ADMIN_URL, create_postgres_test_schema, drop_postgres_test_schema

PREFIX = "cauldra_bdoff"


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


@unittest.skipUnless(ADMIN_URL, "TEST_POSTGRES_ADMIN_URL is not configured")
class OfflineBusinessDayPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pg = create_postgres_test_schema(PREFIX, {"SUPPLY_AI_AUTO_CREATE_SCHEMA": "false"})
        cls.main = cls.pg.main
        from fastapi.testclient import TestClient
        cls.client = TestClient(cls.main.app)

    @classmethod
    def tearDownClass(cls):
        drop_postgres_test_schema(cls.pg, PREFIX)

    def _tenant(self, overrides=None, issued_hours_ago=1):
        m = self.main
        db = m.SessionLocal()
        business = m.BusinessProfile(business_code=f"BD-{uuid.uuid4().hex[:10]}", company_name="Offline Day Shop",
                                     country="Nigeria", country_code="NG", currency="NGN", timezone="Africa/Lagos",
                                     subscription_plan="starter", billing_interval="monthly")
        db.add(business); db.flush()
        db.add(m.BusinessSubscription(business_id=business.id, plan="starter", billing_interval="monthly",
                                      status="active", payment_status="paid",
                                      current_period_start=_now() - timedelta(days=1),
                                      current_period_end=_now() + timedelta(days=29)))
        location = m.Location(business_id=business.id, name="Main", is_main=True, is_active=True,
                              country="Nigeria", country_code="NG", currency="NGN", timezone="Africa/Lagos")
        db.add(location); db.flush()
        warehouse = m.Warehouse(business_id=business.id, name="Main Central Warehouse", is_active=True, location_id=location.id)
        db.add(warehouse); db.flush()
        users = {}
        for role in ("admin", "staff"):
            user = m.User(username=f"{role}-{uuid.uuid4().hex[:8]}", password=m.hash_password("OfflineDay9pass"), role=role,
                          firstname="Bd", lastname=role.title(), email=f"{role}-{uuid.uuid4().hex[:8]}@example.com",
                          phone="08000000000", business_id=business.id,
                          permission_overrides=json.dumps(overrides) if (overrides and role == "staff") else None)
            db.add(user); db.flush()
            users[role] = user
        product = m.Product(sku=f"SKU-{uuid.uuid4().hex[:10]}", name="Rice 5kg", category="Test", quantity=20,
                            min_stock_level=0, cost_price=10.0, wholesale_price=15.0, retail_price=20.0,
                            warehouse="Main Central Warehouse", initial_stock=20, business_id=business.id,
                            owner_id=users["admin"].id)
        db.add(product); db.flush()
        db.add(m.WarehouseStock(business_id=business.id, product_id=product.id, warehouse="Main Central Warehouse",
                                warehouse_id=warehouse.id, quantity=20))
        tenant = {"business_id": business.id, "location_id": location.id, "warehouse_id": warehouse.id,
                  "product_id": product.id, "users": {}}
        for role, user in users.items():
            device_id = str(uuid.uuid4())
            perms = hashlib.sha256(json.dumps(m.get_effective_permissions(user), sort_keys=True).encode()).hexdigest()
            db.add(m.OfflineDevice(device_id=device_id, business_id=business.id, user_id=user.id,
                                   auth_version=int(user.auth_version or 1), role=user.role, permissions_hash=perms,
                                   issued_at=_now() - timedelta(hours=issued_hours_ago), expires_at=_now() + timedelta(days=1)))
            tenant["users"][role] = {"id": user.id, "auth_version": int(user.auth_version or 1), "device_id": device_id,
                                     "token": m.issue_token(user, db)}
        db.commit(); db.close()
        return tenant

    def _replay(self, tenant, role, op_type, payload, captured=None, op_id=None):
        u = tenant["users"][role]
        body = {"schema_version": 2, "op_id": op_id or str(uuid.uuid4()), "device_id": u["device_id"],
                "business_id": tenant["business_id"], "user_id": u["id"], "auth_version": u["auth_version"],
                "type": op_type, "captured_at": (captured or _now()).isoformat() + "Z", "payload": payload}
        return self.client.post("/offline/replay", json=body, headers={"Authorization": f"Bearer {u['token']}"})

    def _sale(self, tenant, day_id, role="staff"):
        return self._replay(tenant, role, "sale_checkout", {
            "items": [{"product_id": tenant["product_id"], "quantity": 2, "warehouse_id": tenant["warehouse_id"],
                       "price_mode": "retail", "unit_price": 20.0}],
            "location_id": tenant["location_id"], "business_day_id": day_id, "currency": "NGN"})

    def _open_days(self, tenant):
        m = self.main
        db = m.SessionLocal()
        try:
            return db.query(m.BusinessDay).filter_by(business_id=tenant["business_id"], is_open=True).all()
        finally:
            db.close()

    def test_permitted_user_opens_day_offline_and_its_sale_syncs(self):
        tenant = self._tenant()
        opened_offline = _now() - timedelta(minutes=40)
        op_id = str(uuid.uuid4())
        r = self._replay(tenant, "staff", "business_day_open", {"location_id": tenant["location_id"]}, opened_offline, op_id)
        self.assertEqual(r.status_code, 200, r.text)
        result = r.json()["result"]
        self.assertFalse(result["joined_existing"])
        day_id = result["business_day_id"]

        again = self._replay(tenant, "staff", "business_day_open", {"location_id": tenant["location_id"]}, opened_offline, op_id)
        self.assertEqual(again.status_code, 200, again.text)
        self.assertEqual(again.json()["result"]["business_day_id"], day_id, "a retried change is answered, not repeated")
        self.assertEqual([d.id for d in self._open_days(tenant)], [day_id])

        m = self.main
        db = m.SessionLocal()
        try:
            day = db.get(m.BusinessDay, day_id)
            self.assertLess(abs((day.opened_at - opened_offline).total_seconds()), 1, "keeps the real offline open time")
            self.assertEqual(day.location_id, tenant["location_id"])
            self.assertEqual(day.opened_by_id, tenant["users"]["staff"]["id"])
            audit = db.query(m.AuditLog).filter_by(business_id=tenant["business_id"], action="BUSINESS_DAY_STARTED").one()
            meta = json.loads(audit.metadata_json)
            self.assertTrue(meta["offline"])
            self.assertEqual(meta["offline_ref"], op_id)
        finally:
            db.close()

        sale = self._sale(tenant, day_id)
        self.assertEqual(sale.status_code, 200, sale.text)
        self.assertEqual(sale.json()["result"]["business_day_id"], day_id)

    def test_user_without_business_day_permission_is_refused(self):
        tenant = self._tenant(overrides={"business_day.manage": False})
        r = self._replay(tenant, "staff", "business_day_open", {"location_id": tenant["location_id"]})
        self.assertEqual(r.status_code, 403, r.text)
        self.assertEqual(self._open_days(tenant), [])

    def test_day_already_opened_today_by_another_device_is_joined(self):
        tenant = self._tenant()
        online = self.client.post(f"/sales/start-business-day?location_id={tenant['location_id']}",
                                  headers={"Authorization": f"Bearer {tenant['users']['admin']['token']}"})
        self.assertEqual(online.status_code, 200, online.text)
        server_day = online.json()["business_day"]["id"]

        r = self._replay(tenant, "staff", "business_day_open", {"location_id": tenant["location_id"]},
                         _now() - timedelta(minutes=5))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["result"], {**r.json()["result"], "business_day_id": server_day, "joined_existing": True})
        self.assertEqual([d.id for d in self._open_days(tenant)], [server_day], "never two open days at one location")

        sale = self._sale(tenant, server_day)
        self.assertEqual(sale.status_code, 200, sale.text)
        m = self.main
        db = m.SessionLocal()
        try:
            self.assertEqual(db.query(m.AuditLog).filter_by(business_id=tenant["business_id"],
                                                            action="BUSINESS_DAY_OFFLINE_OPEN_JOINED").count(), 1)
        finally:
            db.close()

    def test_open_day_from_a_different_date_is_never_joined(self):
        tenant = self._tenant(issued_hours_ago=72)
        online = self.client.post(f"/sales/start-business-day?location_id={tenant['location_id']}",
                                  headers={"Authorization": f"Bearer {tenant['users']['admin']['token']}"})
        server_day = online.json()["business_day"]["id"]
        r = self._replay(tenant, "staff", "business_day_open", {"location_id": tenant["location_id"]},
                         _now() - timedelta(hours=48))
        self.assertEqual(r.status_code, 409, r.text)
        detail = r.json()["detail"]
        self.assertEqual(detail["code"], "BUSINESS_DAY_CONFLICT")
        self.assertNotEqual(detail["details"]["offline_date"], detail["details"]["open_day_date"])
        self.assertEqual([d.id for d in self._open_days(tenant)], [server_day])

    def test_unknown_location_is_refused(self):
        tenant = self._tenant()
        r = self._replay(tenant, "staff", "business_day_open", {"location_id": 999999})
        self.assertEqual(r.status_code, 409, r.text)
        self.assertEqual(r.json()["detail"]["code"], "LOCATION_CHANGED")
        self.assertEqual(self._open_days(tenant), [])

    # --- offline close -------------------------------------------------------
    def _online_open(self, tenant):
        r = self.client.post(f"/sales/start-business-day?location_id={tenant['location_id']}",
                             headers={"Authorization": f"Bearer {tenant['users']['admin']['token']}"})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["business_day"]["id"]

    def _day(self, day_id):
        m = self.main
        db = m.SessionLocal()
        try:
            return db.get(m.BusinessDay, day_id)
        finally:
            db.close()

    def test_synced_day_closes_offline_with_its_real_close_time(self):
        tenant = self._tenant()
        day_id = self._online_open(tenant)
        m = self.main
        db = m.SessionLocal()
        try:
            db.get(m.BusinessDay, day_id).opened_at = _now() - timedelta(minutes=50)  # opened before going offline
            db.commit()
        finally:
            db.close()
        closed_offline = _now() - timedelta(minutes=10)
        op_id = str(uuid.uuid4())
        payload = {"business_day_id": day_id, "location_id": tenant["location_id"], "own_refs": []}
        r = self._replay(tenant, "staff", "business_day_close", payload, closed_offline, op_id)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(r.json()["result"]["already_closed"])
        again = self._replay(tenant, "staff", "business_day_close", payload, closed_offline, op_id)
        self.assertEqual(again.status_code, 200, again.text)
        self.assertEqual(again.json()["result"]["business_day_id"], day_id, "a retried close is answered, not repeated")
        day = self._day(day_id)
        self.assertFalse(day.is_open)
        self.assertLess(abs((day.closed_at - closed_offline).total_seconds()), 1)
        m = self.main
        db = m.SessionLocal()
        try:
            audits = db.query(m.AuditLog).filter_by(business_day_id=day_id, action="BUSINESS_DAY_CLOSED").all()
            self.assertEqual(len(audits), 1)
            self.assertTrue(json.loads(audits[0].metadata_json)["offline"])
        finally:
            db.close()

    def test_offline_day_open_sale_close_in_order(self):
        tenant = self._tenant()
        opened = self._replay(tenant, "staff", "business_day_open", {"location_id": tenant["location_id"]}, _now() - timedelta(minutes=30))
        day_id = opened.json()["result"]["business_day_id"]
        sale = self._sale(tenant, day_id)
        self.assertEqual(sale.status_code, 200, sale.text)
        sale_ref = sale.json()["op_id"]
        r = self._replay(tenant, "staff", "business_day_close",
                         {"business_day_id": day_id, "location_id": tenant["location_id"], "own_refs": [sale_ref]},
                         _now() - timedelta(minutes=5))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(self._day(day_id).is_open, "own offline sales synced after the offline close time do not block it")
        self.assertEqual(self._open_days(tenant), [])

    def test_day_already_closed_on_server_is_answered_not_changed(self):
        tenant = self._tenant()
        day_id = self._online_open(tenant)
        self.client.post(f"/sales/end-business-day?location_id={tenant['location_id']}",
                         headers={"Authorization": f"Bearer {tenant['users']['admin']['token']}"})
        server_closed = self._day(day_id).closed_at
        r = self._replay(tenant, "staff", "business_day_close",
                         {"business_day_id": day_id, "location_id": tenant["location_id"]}, _now() - timedelta(minutes=1))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["result"]["already_closed"])
        self.assertEqual(self._day(day_id).closed_at, server_closed, "never closed twice or re-timed")
        m = self.main
        db = m.SessionLocal()
        try:
            self.assertEqual(db.query(m.AuditLog).filter_by(business_day_id=day_id, action="BUSINESS_DAY_OFFLINE_CLOSE_ALREADY_CLOSED").count(), 1)
        finally:
            db.close()

    def test_work_recorded_by_others_after_the_offline_close_keeps_the_day_open(self):
        tenant = self._tenant()
        day_id = self._online_open(tenant)
        closed_offline = _now() - timedelta(minutes=10)
        other = self._sale(tenant, day_id, role="admin")  # another device, after the offline close
        self.assertEqual(other.status_code, 200, other.text)
        r = self._replay(tenant, "staff", "business_day_close",
                         {"business_day_id": day_id, "location_id": tenant["location_id"], "own_refs": []}, closed_offline)
        self.assertEqual(r.status_code, 409, r.text)
        self.assertEqual(r.json()["detail"]["code"], "BUSINESS_DAY_CONFLICT")
        self.assertTrue(self._day(day_id).is_open)

    def test_close_needs_business_day_permission(self):
        tenant = self._tenant(overrides={"business_day.manage": False})
        day_id = self._online_open(tenant)
        r = self._replay(tenant, "staff", "business_day_close", {"business_day_id": day_id, "location_id": tenant["location_id"]})
        self.assertEqual(r.status_code, 403, r.text)
        self.assertTrue(self._day(day_id).is_open)

    def test_permission_changed_before_reconnect_refuses_close(self):
        tenant = self._tenant()
        day_id = self._online_open(tenant)
        m = self.main
        db = m.SessionLocal()
        try:
            staff = db.get(m.User, tenant["users"]["staff"]["id"])
            staff.permission_overrides = json.dumps({"reports.sales": True})
            db.commit()
        finally:
            db.close()
        r = self._replay(tenant, "staff", "business_day_close", {"business_day_id": day_id, "location_id": tenant["location_id"]})
        self.assertEqual(r.status_code, 403, r.text)
        self.assertEqual(r.json()["detail"]["code"], "PERMISSION_CHANGED")
        self.assertTrue(self._day(day_id).is_open)

    def test_sale_opens_a_day_with_the_sale_permission_like_online(self):
        tenant = self._tenant(overrides={"business_day.manage": False})
        explicit = self._replay(tenant, "staff", "business_day_open", {"location_id": tenant["location_id"]})
        self.assertEqual(explicit.status_code, 403, "tapping Open Business Day still needs business_day.manage")
        implicit = self._replay(tenant, "staff", "business_day_open", {"location_id": tenant["location_id"], "auto": True, "trigger": "sale"})
        self.assertEqual(implicit.status_code, 200, implicit.text)
        m = self.main
        db = m.SessionLocal()
        try:
            self.assertEqual(db.query(m.AuditLog).filter_by(business_id=tenant["business_id"], action="BUSINESS_DAY_AUTO_OPENED").count(), 1)
        finally:
            db.close()
        no_trigger = self._tenant(overrides={"business_day.manage": False})
        r = self._replay(no_trigger, "staff", "business_day_open", {"location_id": no_trigger["location_id"], "auto": True})
        self.assertEqual(r.status_code, 403, "an auto open must name the sale/expense that needs it")


if __name__ == "__main__":
    unittest.main()
