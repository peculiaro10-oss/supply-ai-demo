"""NATIVE-SESSION-001 — the Android app keeps its sign-in across process death.

Reproduced on a Galaxy A23: every /auth/refresh rotates the HttpOnly refresh
cookie and revokes its predecessor, while the Android WebView writes cookies to
disk only in ~30 s batches. A process death in that window left the revoked
predecessor on disk and the next launch was signed out (204). The fix flushes
the WebView cookie store right after every auth response that sets, rotates or
clears the cookie (CauldraSession.flushCookies), and makes the clearing cookie
carry the attributes the cookie was set with, so the WebView honours it.

`WebViewCookieStore` models one installed app: the page runs at
https://localhost and calls the API cross-site, so a Set-Cookie is only taken
when it is SameSite=None; Secure (the browser rule that made the old clearing
cookie a no-op). Responses land in memory; flush() writes memory to disk; die()
keeps only what reached disk. Rotation, reuse rejection and every revocation
path must be unchanged. Disposable SQLite + TestClient, in the style of
tests/test_push_ownership.py.
"""
import os
import tempfile
os.environ['PYTHON_DOTENV_DISABLED'] = '1'
os.environ['SUPPLY_AI_SKIP_DB_STARTUP_CHECK'] = 'true'
os.environ['DATABASE_URL'] = 'postgresql+psycopg://test:test@127.0.0.1:65432/cauldra_test'
os.environ['SUPPLY_AI_SECRET_KEY'] = 'isolated-test-secret-012345678901234567890123456789'
os.environ.setdefault('SUPPLY_AI_UPLOAD_DIR', tempfile.mkdtemp(prefix='cauldra-nsess-'))
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient
import main

PW = 'Strong-Pass-1'
NATIVE_ORIGIN = 'https://localhost'
COOKIE = main.REFRESH_COOKIE_NAME


def parse_set_cookie(header):
    parts = [p.strip() for p in header.split(';')]
    name, _, value = parts[0].partition('=')
    attrs = {}
    for p in parts[1:]:
        k, _, v = p.partition('=')
        attrs[k.strip().lower()] = v.strip()
    return name, value.strip('"'), attrs


class WebViewCookieStore:
    """The refresh cookie as the Android WebView holds it (memory + disk)."""

    def __init__(self):
        self.memory, self.disk = {}, {}
        self.rejected = []

    def take(self, response):
        for header in response.headers.get_list('set-cookie'):
            name, value, attrs = parse_set_cookie(header)
            if name != COOKIE:
                continue
            # Cross-site response (page https://localhost, API elsewhere).
            if attrs.get('samesite', '').lower() != 'none' or 'secure' not in attrs:
                self.rejected.append(header)
                continue
            if attrs.get('max-age') == '0' or value == '':
                self.memory.pop(name, None)
            else:
                self.memory[name] = value

    def flush(self):                      # CauldraSession.flushCookies()
        self.disk = dict(self.memory)

    def die(self):                        # process death before the ~30 s batch
        self.memory = dict(self.disk)


class NativeSessionPersistence(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        main.Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

        def db_dep():
            with self.Session() as session:
                yield session
        main.app.dependency_overrides[main.get_db] = db_dep
        self.db = self.Session()
        self.patches = [
            patch.object(main, 'send_resend_email', lambda **k: None),
            patch.object(main, 'RESEND_FROM', 'Cauldra <no-reply@example.com>'),
            patch.dict(os.environ, {'RESEND_API_KEY': 're_test_only'}),
            patch.object(main, 'background_session_factory', lambda: self.Session()),
            patch.object(main, 'PAYSTACK_SECRET_KEY', ''),
            patch.object(main, 'ALLOWED_ORIGINS', [NATIVE_ORIGIN]),
            # The reproduced relaunch came long after the rotation; no grace recovery.
            patch.object(main, 'REFRESH_ROTATION_GRACE_SECONDS', 0),
        ]
        for p in self.patches:
            p.start()
        now = datetime.utcnow()
        self.biz = main.BusinessProfile(business_code='NS-1111-11', company_name='Native Session', currency='NGN (₦)',
                                        subscription_plan='business', country_code='NG')
        self.db.add(self.biz); self.db.commit()
        self.db.add(main.BusinessSubscription(business_id=self.biz.id, plan='business', billing_interval='monthly', status='active',
                                              current_period_start=now, current_period_end=now + timedelta(days=30), card_verified=True))
        self.db.commit()
        self.admin = self.make_user('ns_admin', 'admin')
        self.manager = self.make_user('ns_manager', 'manager')

    def tearDown(self):
        main.app.dependency_overrides.clear()
        for p in self.patches:
            p.stop()
        self.db.close()
        self.engine.dispose()

    # ---------------------------------------------------------------- helpers
    def make_user(self, username, role):
        u = main.User(username=username, email=f'{username}@example.com', password=main.hash_password(PW), phone='+2348030000000',
                      role=role, business_id=self.biz.id, must_change_password=False, auth_version=1)
        self.db.add(u); self.db.commit()
        return u

    def call(self, store, method, path, token=None, json=None, origin=NATIVE_ORIGIN):
        """One request from the app: the cookie it currently holds, its Origin, and the response applied to its store."""
        client = TestClient(main.app, base_url='https://testserver')
        headers = {'Origin': origin} if origin else {}
        if store.memory.get(COOKIE):
            headers['Cookie'] = f'{COOKIE}={store.memory[COOKIE]}'
        if token:
            headers['Authorization'] = f'Bearer {token}'
        r = client.request(method, path, headers=headers, json=json)
        store.take(r)
        return r

    def sign_in(self, store, user, password=PW):
        if user.role == 'admin':
            body = {'business_id': self.biz.business_code, 'username': user.username, 'password': password}
            r = self.call(store, 'POST', '/auth/admin-login', json=body)
        else:
            body = {'business_id': self.biz.business_code, 'username': user.username, 'password': password, 'selected_role': user.role}
            r = self.call(store, 'POST', '/auth/employee-login', json=body)
        self.assertEqual(r.status_code, 200, r.text)
        store.flush()                                  # the fix: right after sign-in
        return r.json()['access_token']

    def refresh(self, store, flush=True):
        r = self.call(store, 'POST', '/auth/refresh')
        if flush:
            store.flush()                              # the fix: right after every refresh (200 or 204)
        return r

    def relaunch(self, store):
        store.die()
        return self.refresh(store)

    def live_rows(self, user):
        self.db.expire_all()
        return self.db.query(main.RefreshSession).filter(main.RefreshSession.user_id == user.id,
                                                         main.RefreshSession.revoked_at.is_(None)).all()

    def signed_in_after_revocation(self, revoke):
        """Sign the manager in on the app, persist a rotation, revoke through `revoke`, relaunch."""
        app = WebViewCookieStore()
        token = self.sign_in(app, self.manager)
        self.assertEqual(self.refresh(app).status_code, 200)
        revoke(app, token)
        r = self.relaunch(app)
        return r, app

    # ---------------------------------------------------------------- the reproduced race
    def test_rotation_then_death_before_the_batch_restores_the_successor(self):
        app = WebViewCookieStore()
        self.sign_in(app, self.admin)
        r = self.refresh(app)                          # resume: rotation, then the flush
        self.assertEqual(r.status_code, 200, r.text)
        successor = app.memory[COOKIE]
        r = self.relaunch(app)                         # process death before the ~30 s batch
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()['id'], r.json()['business_id']), (self.admin.id, self.biz.id))
        self.assertNotEqual(app.memory[COOKIE], successor)       # restored and rotated again
        live = self.live_rows(self.admin)
        self.assertEqual(len(live), 1)
        self.assertEqual(live[0].token_hash, main.hash_text(app.memory[COOKIE]))

    def test_without_the_flush_the_same_race_signs_out(self):
        """The defect as reproduced, and proof that rotation/reuse protection is not relaxed:
        the revoked predecessor on disk is still rejected."""
        app = WebViewCookieStore()
        self.sign_in(app, self.admin)
        self.assertEqual(self.refresh(app, flush=False).status_code, 200)
        r = self.relaunch(app)
        self.assertEqual(r.status_code, 204)
        self.assertEqual(len(self.live_rows(self.admin)), 1)     # the successor, orphaned on the server

    def test_repeated_resumes_and_deaths_keep_one_live_session(self):
        app = WebViewCookieStore()
        self.sign_in(app, self.admin)
        for _ in range(5):
            self.assertEqual(self.refresh(app).status_code, 200)
            self.assertEqual(self.relaunch(app).status_code, 200)
        self.assertEqual(len(self.live_rows(self.admin)), 1)

    def test_a_rotated_predecessor_is_still_rejected_everywhere(self):
        app = WebViewCookieStore()
        self.sign_in(app, self.admin)
        stolen = dict(app.memory)
        self.assertEqual(self.refresh(app).status_code, 200)
        thief = WebViewCookieStore(); thief.memory = stolen
        self.assertEqual(self.refresh(thief).status_code, 204)
        self.assertEqual(self.relaunch(app).status_code, 200)     # the real device is unaffected

    # ---------------------------------------------------------------- the clearing cookie
    def test_native_clearing_cookie_matches_the_native_set_cookie(self):
        app = WebViewCookieStore()
        token = self.sign_in(app, self.admin)
        r = self.call(app, 'POST', '/auth/logout', token=token)
        self.assertEqual(r.status_code, 200)
        [header] = [h for h in r.headers.get_list('set-cookie') if h.startswith(COOKIE + '=')]
        _, _, attrs = parse_set_cookie(header)
        self.assertEqual(attrs.get('max-age'), '0')
        self.assertEqual(attrs.get('samesite', '').lower(), 'none')
        self.assertIn('secure', attrs)
        self.assertIn('httponly', attrs)
        self.assertEqual(attrs.get('path'), '/')
        self.assertNotIn('domain', attrs)
        self.assertEqual(app.rejected, [])
        self.assertNotIn(COOKIE, app.memory)                    # the WebView actually drops it
        app.flush(); app.die()
        self.assertNotIn(COOKIE, app.memory)

    def test_a_rejected_refresh_clears_the_dead_cookie_on_the_device(self):
        app = WebViewCookieStore()
        self.sign_in(app, self.admin)
        self.db.query(main.RefreshSession).update({main.RefreshSession.revoked_at: datetime.utcnow()}); self.db.commit()
        self.assertEqual(self.relaunch(app).status_code, 204)
        self.assertEqual(app.rejected, [])
        self.assertNotIn(COOKIE, app.disk)

    def test_web_clearing_cookie_matches_the_web_set_cookie(self):
        web = TestClient(main.app, base_url='https://testserver')
        r = web.post('/auth/admin-login', json={'business_id': self.biz.business_code, 'username': 'ns_admin', 'password': PW})
        set_attrs = parse_set_cookie([h for h in r.headers.get_list('set-cookie') if h.startswith(COOKIE + '=')][0])[2]
        r = web.post('/auth/logout', headers={'Authorization': f"Bearer {r.json()['access_token']}"})
        clear_attrs = parse_set_cookie([h for h in r.headers.get_list('set-cookie') if h.startswith(COOKIE + '=')][0])[2]
        self.assertEqual(clear_attrs.get('samesite', '').lower(), set_attrs.get('samesite', '').lower())
        self.assertEqual('secure' in clear_attrs, 'secure' in set_attrs)
        self.assertEqual((clear_attrs.get('path'), clear_attrs.get('max-age')), ('/', '0'))

    # ---------------------------------------------------------------- revocation is never restored
    def test_logout_is_not_restored(self):
        r, app = self.signed_in_after_revocation(
            lambda app, token: self.assertEqual(self.call(app, 'POST', '/auth/logout', token=token).status_code, 200))
        self.assertEqual(r.status_code, 204)

    def test_logout_is_not_restored_even_if_the_device_kept_the_cookie(self):
        """Older builds could not clear the cookie; the server still refuses it."""
        app = WebViewCookieStore()
        token = self.sign_in(app, self.manager)
        kept = dict(app.memory)
        self.assertEqual(self.call(app, 'POST', '/auth/logout', token=token).status_code, 200)
        app.memory = kept; app.flush()
        self.assertEqual(self.relaunch(app).status_code, 204)

    def test_own_password_change_on_another_device_is_not_restored(self):
        def change(app, token):
            other = WebViewCookieStore()
            t = self.sign_in(other, self.manager)
            r = self.call(other, 'POST', '/auth/change-password', token=t, json={'current_password': PW, 'new_password': 'Newer-Pass-22'})
            self.assertEqual(r.status_code, 200, r.text)
        r, _ = self.signed_in_after_revocation(change)
        self.assertEqual(r.status_code, 204)

    def test_own_password_change_keeps_the_changing_device_signed_in(self):
        app = WebViewCookieStore()
        token = self.sign_in(app, self.manager)
        r = self.call(app, 'POST', '/auth/change-password', token=token, json={'current_password': PW, 'new_password': 'Newer-Pass-22'})
        self.assertEqual(r.status_code, 200, r.text)
        app.flush()                                    # the fix: after the password change
        self.assertEqual(self.relaunch(app).status_code, 200)

    def test_password_reset_is_not_restored(self):
        def reset(app, token):
            with self.Session() as s:                  # the code path /auth/reset-password runs
                main.revoke_all_user_sessions(s, s.get(main.User, self.manager.id)); s.commit()
        r, _ = self.signed_in_after_revocation(reset)
        self.assertEqual(r.status_code, 204)

    def owner_action(self, method, path, json=None):
        owner = WebViewCookieStore()
        t = self.sign_in(owner, self.admin)
        r = self.call(owner, method, path, token=t, json=json)
        self.assertEqual(r.status_code, 200, r.text)

    def test_admin_reset_is_not_restored(self):
        r, _ = self.signed_in_after_revocation(
            lambda app, token: self.owner_action('PATCH', f'/users/{self.manager.id}/reset-password', {'new_password': 'TempReset9x'}))
        self.assertEqual(r.status_code, 204)

    def test_role_change_is_not_restored(self):
        r, _ = self.signed_in_after_revocation(
            lambda app, token: self.owner_action('PATCH', f'/users/{self.manager.id}/role', {'role': 'staff'}))
        self.assertEqual(r.status_code, 204)

    def test_disable_is_not_restored(self):
        r, _ = self.signed_in_after_revocation(
            lambda app, token: self.owner_action('PATCH', f'/users/{self.manager.id}/disable'))
        self.assertEqual(r.status_code, 204)

    def test_deleted_account_is_not_restored(self):
        r, _ = self.signed_in_after_revocation(
            lambda app, token: self.owner_action('DELETE', f'/users/{self.manager.id}'))
        self.assertEqual(r.status_code, 204)

    def test_expired_refresh_session_is_not_restored(self):
        def expire(app, token):
            self.db.query(main.RefreshSession).update({main.RefreshSession.expires_at: datetime.utcnow() - timedelta(minutes=1)})
            self.db.commit()
        r, _ = self.signed_in_after_revocation(expire)
        self.assertEqual(r.status_code, 204)

    def test_a_restored_temporary_password_session_stays_gated(self):
        self.owner_action('PATCH', f'/users/{self.manager.id}/reset-password', {'new_password': 'TempReset9x'})
        app = WebViewCookieStore()
        self.sign_in(app, self.manager, password='TempReset9x')
        self.assertEqual(self.refresh(app).status_code, 200)
        r = self.relaunch(app)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()['must_change_password'])
        gated = self.call(app, 'GET', '/products/', token=r.json()['access_token'])
        self.assertEqual(gated.status_code, 403)
        self.assertEqual(gated.json()['detail'], main.TEMP_PASSWORD_GATE_DETAIL)


if __name__ == '__main__':
    unittest.main()
