"""Tests for the refresh-token rotation race fix (see auth_refresh() in main.py).

Runs against a real, isolated PostgreSQL schema shared for the whole module
(see tests/postgres_test_support.py — the same create-schema/alembic-upgrade/
drop-schema pattern tests/test_mutation_idempotency_postgres.py already
uses). Each test's own setUp() creates a brand-new, uniquely-named business/
user, so tests never interfere with each other despite sharing one schema.
"""
from __future__ import annotations

import unittest
import uuid
from datetime import timedelta

from postgres_test_support import ADMIN_URL, create_postgres_test_schema, drop_postgres_test_schema

_ctx = None
main = None


def setUpModule():
    global _ctx, main
    if not ADMIN_URL:
        return
    # SUPPLY_AI_REFRESH_COOKIE_SECURE=false: without this the refresh cookie
    # is marked Secure, which TestClient's http://testserver would then
    # correctly withhold on the follow-up request, breaking the
    # login-then-reload flow for a reason unrelated to what's actually being
    # tested here.
    _ctx = create_postgres_test_schema("cauldra_authrt", extra_env={"SUPPLY_AI_REFRESH_COOKIE_SECURE": "false"})
    main = _ctx.main


def tearDownModule():
    if _ctx is not None:
        drop_postgres_test_schema(_ctx, "cauldra_authrt")


@unittest.skipUnless(ADMIN_URL, "TEST_POSTGRES_ADMIN_URL is not configured")
class RefreshRotationTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient

        suffix = uuid.uuid4().hex[:10]
        db = main.SessionLocal()
        biz = main.BusinessProfile(business_code=f"RT-{suffix}", company_name="RT Co")
        db.add(biz)
        db.flush()
        user = main.User(username=f"Owner {suffix}", password=main.hash_password("OwnerPass9"), role="admin",
                          email=f"owner-{suffix}@test.com", phone="1", business_id=biz.id, disabled=False)
        db.add(user)
        db.commit()

        self.main = main
        self.db = db
        self.biz = biz
        self.user = user
        self.client = TestClient(main.app)

    def tearDown(self):
        self.db.close()

    @staticmethod
    def has_set_cookie(resp):
        return "set-cookie" in {k.lower() for k in resp.headers.keys()}

    def test_1_normal_refresh(self):
        raw = main.create_refresh_session(self.db, self.user)
        r = self.client.post("/auth/refresh", cookies={main.REFRESH_COOKIE_NAME: raw})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json().get("access_token"))
        self.assertTrue(self.has_set_cookie(r), "expected a new refresh cookie to be set")
        new_cookie = r.cookies.get(main.REFRESH_COOKIE_NAME)
        self.assertTrue(new_cookie and new_cookie != raw)

    def test_2_second_refresh_with_same_old_token_recovers(self):
        raw = main.create_refresh_session(self.db, self.user)
        r1 = self.client.post("/auth/refresh", cookies={main.REFRESH_COOKIE_NAME: raw})
        self.assertEqual(r1.status_code, 200, r1.text)

        # Same OLD token presented again (as a second tab would, unaware of
        # the rotation r1 just performed) -- must recover, not log out.
        r2 = self.client.post("/auth/refresh", cookies={main.REFRESH_COOKIE_NAME: raw})
        self.assertEqual(r2.status_code, 200, r2.text)
        self.assertTrue(r2.json().get("access_token"))
        self.assertEqual(r2.json()["username"], self.user.username)
        # Critical: r2 must NOT clear or overwrite the cookie r1 just set.
        self.assertFalse(self.has_set_cookie(r2), "grace recovery must not touch the cookie at all")

    def test_3_old_token_after_grace_window_rejected(self):
        raw = main.create_refresh_session(self.db, self.user)
        r1 = self.client.post("/auth/refresh", cookies={main.REFRESH_COOKIE_NAME: raw})
        self.assertEqual(r1.status_code, 200, r1.text)

        # Simulate the grace window having elapsed, without actually sleeping.
        row = self.db.query(main.RefreshSession).filter(main.RefreshSession.token_hash == main.hash_text(raw)).first()
        row.revoked_at = row.revoked_at - timedelta(seconds=main.REFRESH_ROTATION_GRACE_SECONDS + 5)
        self.db.commit()

        r2 = self.client.post("/auth/refresh", cookies={main.REFRESH_COOKIE_NAME: raw})
        self.assertEqual(r2.status_code, 204, r2.text)
        self.assertTrue(self.has_set_cookie(r2), "expired-grace token must have its cookie cleared")

    def test_4_revoked_token_with_no_replacement_rejected(self):
        raw = main.create_refresh_session(self.db, self.user)
        row = self.db.query(main.RefreshSession).filter(main.RefreshSession.token_hash == main.hash_text(raw)).first()
        row.revoked_at = main.datetime.utcnow()  # e.g. an explicit logout -- no replaced_by_hash set
        self.db.commit()

        r = self.client.post("/auth/refresh", cookies={main.REFRESH_COOKIE_NAME: raw})
        self.assertEqual(r.status_code, 204, r.text)
        self.assertTrue(self.has_set_cookie(r))

    def test_5_revoked_token_whose_replacement_is_invalid_rejected(self):
        raw_a = main.create_refresh_session(self.db, self.user)
        r1 = self.client.post("/auth/refresh", cookies={main.REFRESH_COOKIE_NAME: raw_a})
        self.assertEqual(r1.status_code, 200, r1.text)
        raw_b = r1.cookies.get(main.REFRESH_COOKIE_NAME)

        # Independently invalidate B (e.g. the user logged out from the tab that got B).
        row_b = self.db.query(main.RefreshSession).filter(main.RefreshSession.token_hash == main.hash_text(raw_b)).first()
        row_b.revoked_at = main.datetime.utcnow()
        self.db.commit()

        # Presenting A (still within grace) must NOT recover through a now-invalid B.
        r2 = self.client.post("/auth/refresh", cookies={main.REFRESH_COOKIE_NAME: raw_a})
        self.assertEqual(r2.status_code, 204, r2.text)
        self.assertTrue(self.has_set_cookie(r2))

    def test_6_multi_generation_chain_not_walked(self):
        raw_a = main.create_refresh_session(self.db, self.user)
        r1 = self.client.post("/auth/refresh", cookies={main.REFRESH_COOKIE_NAME: raw_a})
        self.assertEqual(r1.status_code, 200, r1.text)
        raw_b = r1.cookies.get(main.REFRESH_COOKIE_NAME)

        # B itself gets rotated too (a legitimate further refresh), producing C.
        r2 = self.client.post("/auth/refresh", cookies={main.REFRESH_COOKIE_NAME: raw_b})
        self.assertEqual(r2.status_code, 200, r2.text)

        # Presenting the ORIGINAL token A must not walk A -> B -> C. B is now
        # itself revoked, so A's one-hop recovery attempt (A -> B) must fail.
        r3 = self.client.post("/auth/refresh", cookies={main.REFRESH_COOKIE_NAME: raw_a})
        self.assertEqual(r3.status_code, 204, r3.text)
        self.assertTrue(self.has_set_cookie(r3))

    def test_7_expired_replacement_rejected(self):
        import secrets as _secrets

        raw_a = main.create_refresh_session(self.db, self.user)
        row_a = self.db.query(main.RefreshSession).filter(main.RefreshSession.token_hash == main.hash_text(raw_a)).first()

        # Hand-construct an already-expired "replacement" and point A at it
        # directly, simulating the moment right after a rotation whose new
        # session is (for whatever reason) already expired.
        raw_b = _secrets.token_urlsafe(48)
        row_b = main.RefreshSession(token_hash=main.hash_text(raw_b), user_id=self.user.id, business_id=self.biz.id,
                                     expires_at=main.datetime.utcnow() - timedelta(seconds=5))
        self.db.add(row_b)
        row_a.revoked_at = main.datetime.utcnow()
        row_a.replaced_by_hash = main.hash_text(raw_b)
        self.db.commit()

        r = self.client.post("/auth/refresh", cookies={main.REFRESH_COOKIE_NAME: raw_a})
        self.assertEqual(r.status_code, 204, r.text)
        self.assertTrue(self.has_set_cookie(r))

    def test_8_and_11_concurrent_refresh_from_two_tabs_survives(self):
        import threading
        from fastapi.testclient import TestClient

        raw = main.create_refresh_session(self.db, self.user)

        # Two independent TestClients with separate cookie jars, both starting
        # from the SAME raw refresh token -- simulating two browser tabs
        # sharing one cookie jar at the instant both decide to refresh.
        client_a = TestClient(main.app)
        client_b = TestClient(main.app)

        results = {}

        def do_refresh(name, c):
            results[name] = c.post("/auth/refresh", cookies={main.REFRESH_COOKIE_NAME: raw})

        ta = threading.Thread(target=do_refresh, args=("a", client_a))
        tb = threading.Thread(target=do_refresh, args=("b", client_b))
        ta.start()
        tb.start()
        ta.join()
        tb.join()

        ra, rb = results["a"], results["b"]
        self.assertEqual(ra.status_code, 200, ra.text)
        self.assertEqual(rb.status_code, 200, rb.text)
        self.assertTrue(ra.json().get("access_token"))
        self.assertTrue(rb.json().get("access_token"))

        # Exactly one of the two actually rotated (got a new cookie); the
        # other recovered silently without touching the cookie at all.
        cookie_setters = [r for r in (ra, rb) if self.has_set_cookie(r)]
        self.assertEqual(len(cookie_setters), 1, f"expected exactly one response to set a cookie, got {len(cookie_setters)}")

        # No corrupted/duplicated state: exactly one NEW RefreshSession row
        # exists beyond the original (i.e. no double-rotation, no orphaned
        # extra rows).
        total_rows = self.db.query(main.RefreshSession).filter(main.RefreshSession.user_id == self.user.id).count()
        self.assertEqual(total_rows, 2, f"expected exactly 2 rows (original + one rotation), found {total_rows}")

        # The session that survived in "the browser" (the winning cookie)
        # still works for a subsequent reload.
        winning_cookie = cookie_setters[0].cookies.get(main.REFRESH_COOKIE_NAME)
        r3 = self.client.post("/auth/refresh", cookies={main.REFRESH_COOKIE_NAME: winning_cookie})
        self.assertEqual(r3.status_code, 200, r3.text)

    def test_9_normal_logout_unchanged(self):
        r_login = self.client.post("/auth/admin-login", json={"business_id": self.biz.business_code, "username": self.user.username, "password": "OwnerPass9"})
        self.assertEqual(r_login.status_code, 200, r_login.text)
        token = r_login.json()["access_token"]

        r_logout = self.client.post("/auth/logout", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(r_logout.status_code, 200, r_logout.text)

        # The cookie set at login must now be rejected/cleared on refresh.
        r_refresh = self.client.post("/auth/refresh")
        self.assertEqual(r_refresh.status_code, 204, r_refresh.text)

    def test_10_login_then_reload_unchanged(self):
        r_login = self.client.post("/auth/admin-login", json={"business_id": self.biz.business_code, "username": self.user.username, "password": "OwnerPass9"})
        self.assertEqual(r_login.status_code, 200, r_login.text)

        # Simulate a page reload: a fresh request presenting only the cookie
        # TestClient already captured from login, exactly like a real browser reload.
        r_reload = self.client.post("/auth/refresh")
        self.assertEqual(r_reload.status_code, 200, r_reload.text)
        self.assertTrue(r_reload.json().get("access_token"))


if __name__ == "__main__":
    unittest.main()
