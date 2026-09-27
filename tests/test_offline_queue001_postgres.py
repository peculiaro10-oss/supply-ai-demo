"""OFFLINE-QUEUE-001 (triage 78) through the real /offline/replay endpoint:
a queued change the server refuses for a bad value comes back with a reason
naming the field (stored by the client as the change's last_error).

Set TEST_POSTGRES_ADMIN_URL (see tests/postgres_test_support.py).
"""
from __future__ import annotations

import hashlib
import json
import unittest
import uuid
from datetime import datetime, timedelta, timezone

from tests.postgres_test_support import ADMIN_URL, create_postgres_test_schema, drop_postgres_test_schema

PREFIX = "cauldra_oq001"


@unittest.skipUnless(ADMIN_URL, "TEST_POSTGRES_ADMIN_URL is not configured")
class OfflineReplayReasonPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pg = create_postgres_test_schema(PREFIX, {"SUPPLY_AI_AUTO_CREATE_SCHEMA": "false"})
        cls.main = cls.pg.main

    @classmethod
    def tearDownClass(cls):
        drop_postgres_test_schema(cls.pg, PREFIX)

    def test_refused_value_reason_names_the_field(self):
        from fastapi.testclient import TestClient
        m = self.main
        db = m.SessionLocal()
        business = m.BusinessProfile(business_code=f"OQ-{uuid.uuid4().hex[:10]}", company_name="Offline Queue",
                                     country_code="NG", subscription_plan="starter", billing_interval="monthly")
        db.add(business); db.flush()
        user = m.User(username=f"admin-{uuid.uuid4().hex[:8]}", password=m.hash_password("OfflineQ9pass"), role="admin",
                      firstname="Ada", lastname="Obi", email=f"{uuid.uuid4().hex[:8]}@example.com",
                      phone="+2348000000000", business_id=business.id)
        db.add(user); db.flush()
        db.add(m.Warehouse(business_id=business.id, name="Main", is_active=True))
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        device_id = str(uuid.uuid4())
        perms = hashlib.sha256(json.dumps(m.get_effective_permissions(user), sort_keys=True).encode()).hexdigest()
        db.add(m.OfflineDevice(device_id=device_id, business_id=business.id, user_id=user.id,
                               auth_version=int(user.auth_version or 1), role=user.role, permissions_hash=perms,
                               issued_at=now - timedelta(hours=1), expires_at=now + timedelta(days=1)))
        token = m.issue_token(user, db)
        ids = (business.id, user.id, int(user.auth_version or 1))
        db.commit(); db.close()

        body = {"schema_version": 2, "op_id": str(uuid.uuid4()), "device_id": device_id, "business_id": ids[0],
                "user_id": ids[1], "auth_version": ids[2], "type": "product_create",
                "captured_at": now.isoformat() + "Z",
                "payload": {"name": "Rice", "category": "Food", "quantity": "plenty-4412", "min_stock_level": 1,
                            "cost_price": 100, "retail_price": 150, "warehouse": "Main"}}
        with TestClient(m.app) as client:
            r = client.post("/offline/replay", json=body, headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(r.status_code, 422, r.text)
        detail = r.json()["detail"]
        self.assertEqual(detail["code"], "VALIDATION_ERROR")
        self.assertIn("invalid quantity", detail["message"])
        self.assertEqual(detail["details"]["fields"][0]["field"], "quantity")
        self.assertNotIn("plenty-4412", r.text)


if __name__ == "__main__":
    unittest.main()
