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
- an unknown location is refused.

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


if __name__ == "__main__":
    unittest.main()
