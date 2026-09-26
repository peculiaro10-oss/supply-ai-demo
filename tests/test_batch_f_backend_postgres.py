"""Batch F backend checks that need a real database (PostgreSQL).

AUD-001: re-sending an unchanged profile value (the app re-sends the current
language on every load) must not write a "profile updated" activity entry.

Set TEST_POSTGRES_ADMIN_URL (see tests/postgres_test_support.py).
"""
from __future__ import annotations

import unittest
import uuid

from tests.postgres_test_support import ADMIN_URL, create_postgres_test_schema, drop_postgres_test_schema

PREFIX = "cauldra_batchf"


@unittest.skipUnless(ADMIN_URL, "TEST_POSTGRES_ADMIN_URL is not configured")
class ProfileAuditPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pg = create_postgres_test_schema(PREFIX, {"SUPPLY_AI_AUTO_CREATE_SCHEMA": "false"})
        cls.main = cls.pg.main

    @classmethod
    def tearDownClass(cls):
        drop_postgres_test_schema(cls.pg, PREFIX)

    def _user(self):
        m = self.main
        db = m.SessionLocal()
        business = m.BusinessProfile(business_code=f"BF-{uuid.uuid4().hex[:10]}", company_name="Batch F",
                                     country_code="NG", subscription_plan="starter", billing_interval="monthly")
        db.add(business); db.flush()
        user = m.User(username=f"admin-{uuid.uuid4().hex[:8]}", password=m.hash_password("BatchFPass9"), role="admin",
                      firstname="Ada", lastname="Obi", email=f"{uuid.uuid4().hex[:8]}@example.com",
                      phone="+2348000000000", business_id=business.id)
        db.add(user); db.flush()
        token = m.issue_token(user, db)
        result = (user.id, token)
        db.commit(); db.close()
        return result

    def _profile_entries(self, user_id):
        db = self.main.SessionLocal()
        try:
            return [row.description for row in db.query(self.main.AuditLog)
                    .filter(self.main.AuditLog.actor_user_id == user_id, self.main.AuditLog.action == "USER_PROFILE_UPDATED")
                    .order_by(self.main.AuditLog.id).all()]
        finally:
            db.close()

    def test_unchanged_values_write_no_activity_entry(self):
        from fastapi.testclient import TestClient
        user_id, token = self._user()
        client = TestClient(self.main.app)
        headers = {"Authorization": f"Bearer {token}"}

        first = client.patch("/users/me/profile", json={"preferred_language": "en"}, headers=headers)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(len(self._profile_entries(user_id)), 1, "a real first choice is recorded")

        # What every app load used to do, twice: re-send the same language.
        for _ in range(4):
            again = client.patch("/users/me/profile", json={"preferred_language": "EN "}, headers=headers)
            self.assertEqual(again.status_code, 200, again.text)
        self.assertEqual(len(self._profile_entries(user_id)), 1, "re-sending the same language records nothing")

        same_names = client.patch("/users/me/profile", json={"firstname": "Ada", "lastname": " Obi "}, headers=headers)
        self.assertEqual(same_names.status_code, 200, same_names.text)
        self.assertEqual(len(self._profile_entries(user_id)), 1, "unchanged names record nothing")

        changed = client.patch("/users/me/profile", json={"firstname": "Adaeze", "preferred_language": "en"}, headers=headers)
        self.assertEqual(changed.status_code, 200, changed.text)
        entries = self._profile_entries(user_id)
        self.assertEqual(len(entries), 2)
        self.assertIn("first name", entries[-1])
        self.assertNotIn("preferred language", entries[-1], "only what changed is named")


if __name__ == "__main__":
    unittest.main()
