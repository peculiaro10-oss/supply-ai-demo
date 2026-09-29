"""TMPPW-001 / TMPPW-002 — the temporary-password gate.

While must_change_password is set, every authenticated route answers the gate's
403 except the forced-change screen's own minimum (ALLOWLIST below). The route
sweep enumerates the whole application, so a new route that forgets the gate
fails here. Also covered: the permanent password may not repeat the temporary
one; refresh / second sign-in keep the gate; a successful change releases it and
revokes the temporary sessions; Admin reset re-arms it and revokes live sessions.

Disposable SQLite + TestClient, in the style of tests/test_batch_b_security.py.
"""
import os
import re
import tempfile
os.environ['PYTHON_DOTENV_DISABLED'] = '1'
os.environ['SUPPLY_AI_SKIP_DB_STARTUP_CHECK'] = 'true'
os.environ['DATABASE_URL'] = 'postgresql+psycopg://test:test@127.0.0.1:65432/cauldra_test'
os.environ['SUPPLY_AI_SECRET_KEY'] = 'isolated-test-secret-012345678901234567890123456789'
os.environ.setdefault('SUPPLY_AI_UPLOAD_DIR', tempfile.mkdtemp(prefix='cauldra-tmppw-'))
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
import main

OWNER_PW = 'Owner-Pass-1'
GATE = main.TEMP_PASSWORD_GATE_DETAIL
# The forced-change screen's minimum. Anything else must answer the gate.
ALLOWLIST = {
    ('GET', '/auth/me'),
    ('POST', '/auth/logout'),
    ('POST', '/auth/change-password'),
    ('GET', '/users/me/profile'),
    ('DELETE', '/offline/devices/{device_id}'),
}
AUTH_DEPS = {'get_authenticated_user', 'get_password_settled_user', 'get_current_user', 'get_notification_reader'}


def dependency_names(dependant):
    names = set()
    for sub in dependant.dependencies:
        names.add(getattr(sub.call, '__name__', ''))
        names |= dependency_names(sub)
    return names


def business_user_routes():
    """Every (method, path) whose handler resolves a Cauldra business user."""
    out = []
    for route in main.app.routes:
        if isinstance(route, APIRoute) and AUTH_DEPS & dependency_names(route.dependant):
            out += [(m, route.path) for m in sorted(route.methods) if m not in ('HEAD', 'OPTIONS')]
    return out


class TempPasswordGate(unittest.TestCase):
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
        ]
        for p in self.patches:
            p.start()
        now = datetime.utcnow()
        self.biz = main.BusinessProfile(business_code='TP-1111-11', company_name='Temp PW', currency='NGN (₦)',
                                        subscription_plan='business', country_code='NG')
        self.db.add(self.biz); self.db.commit()
        self.db.add(main.BusinessSubscription(business_id=self.biz.id, plan='business', billing_interval='monthly', status='active',
                                              current_period_start=now, current_period_end=now + timedelta(days=30), card_verified=True))
        self.owner = main.User(username='owner', email='owner@example.com', password=main.hash_password(OWNER_PW), phone='+2348030000000',
                               role='admin', business_id=self.biz.id, must_change_password=False, auth_version=1)
        self.db.add(self.owner); self.db.commit()
        self.oc = self.client()
        r = self.login(self.oc, 'owner', OWNER_PW, 'admin')
        self.assertEqual(r.status_code, 200, r.text)
        self.otok = r.json()['access_token']

    def tearDown(self):
        main.app.dependency_overrides.clear()
        for p in self.patches:
            p.stop()
        self.db.close()
        self.engine.dispose()

    # ---------------------------------------------------------------- helpers
    def client(self):
        return TestClient(main.app, base_url='https://testserver')

    def login(self, c, username, pw, role):
        if role == 'admin':
            return c.post('/auth/admin-login', json={'business_id': 'TP-1111-11', 'username': username, 'password': pw})
        return c.post('/auth/employee-login', json={'business_id': 'TP-1111-11', 'username': username, 'password': pw, 'selected_role': role})

    @staticmethod
    def H(token):
        return {'Authorization': f'Bearer {token}'}

    def create_temp(self, role, pw='tmp123'):
        r = self.oc.post('/users', headers=self.H(self.otok), json={
            'username': f't_{role}', 'password': pw, 'role': role, 'firstname': 'T', 'lastname': role,
            'email': f't_{role}@example.com', 'phone': '08031234567', 'position': role})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()['must_change_password'])
        return pw

    def flag(self, username):
        self.db.expire_all()
        return self.db.query(main.User).filter_by(business_id=self.biz.id, username=username).one().must_change_password

    # ---------------------------------------------------------------- TMPPW-001
    def test_only_the_allowlist_is_open_to_a_temporary_password_token(self):
        routes = business_user_routes()
        self.assertGreater(len(routes), 100)
        for allowed in ALLOWLIST:
            self.assertIn(allowed, routes)
        for role in ('staff', 'manager', 'admin'):
            pw = self.create_temp(role)
            c = self.client()
            r = self.login(c, f't_{role}', pw, role)
            self.assertEqual(r.status_code, 200)
            self.assertTrue(r.json()['must_change_password'])
            token = r.json()['access_token']
            open_routes = []
            for method, path in routes:
                if (method, path) in ALLOWLIST:
                    continue
                with self.subTest(role=role, route=f'{method} {path}'):
                    resp = c.request(method, re.sub(r'\{[^}]+\}', '1', path), headers=self.H(token),
                                     json={} if method in ('POST', 'PUT', 'PATCH') else None)
                    self.assertEqual(resp.status_code, 403, f'{method} {path}: {resp.text[:160]}')
                    self.assertIn(GATE, resp.text)
                    if resp.status_code != 403:
                        open_routes.append(f'{method} {path}')
            for method, path in sorted(ALLOWLIST - {('POST', '/auth/logout'), ('POST', '/auth/change-password'), ('DELETE', '/offline/devices/{device_id}')}):
                self.assertEqual(c.request(method, path, headers=self.H(token)).status_code, 200, path)
            self.assertEqual(c.delete('/offline/devices/00000000-0000-0000-0000-000000000001', headers=self.H(token)).status_code, 200)
            self.assertEqual(open_routes, [])

    def test_temporary_admin_cannot_touch_billing(self):
        pw = self.create_temp('admin')
        c = self.client()
        token = self.login(c, 't_admin', pw, 'admin').json()['access_token']
        for method, path, body in [('POST', '/subscription/cancel', None), ('POST', '/subscription/upgrade-quote', {'plan': 'enterprise', 'billing_interval': 'monthly'}),
                                   ('POST', '/subscription/checkout', {'plan': 'enterprise', 'billing_interval': 'monthly'}), ('GET', '/subscription/payments', None),
                                   ('GET', '/subscription/usage', None), ('GET', '/business-profile/', None), ('PATCH', '/users/me/profile', {'firstname': 'X'})]:
            resp = c.request(method, path, headers=self.H(token), json=body)
            self.assertEqual(resp.status_code, 403, f'{method} {path}')
            self.assertIn(GATE, resp.text)
        self.db.expire_all()
        self.assertFalse(self.db.query(main.BusinessSubscription).filter_by(business_id=self.biz.id).one().cancel_at_period_end)
        self.assertEqual(self.db.query(main.SubscriptionUpgradeQuote).filter_by(business_id=self.biz.id).count(), 0)

    def test_settled_admin_keeps_billing_access(self):
        c = self.client()
        self.assertEqual(c.get('/subscription/usage', headers=self.H(self.otok)).status_code, 200)
        self.assertEqual(c.get('/subscription/payments', headers=self.H(self.otok)).status_code, 200)
        self.assertEqual(c.get('/business-profile/', headers=self.H(self.otok)).status_code, 200)

    def test_gate_survives_refresh_and_a_second_sign_in(self):
        pw = self.create_temp('staff')
        c = self.client()
        self.login(c, 't_staff', pw, 'staff')
        r = c.post('/auth/refresh')
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()['must_change_password'])
        self.assertIn(GATE, c.get('/products/', headers=self.H(r.json()['access_token'])).text)
        second = self.login(self.client(), 't_staff', pw, 'staff')
        self.assertTrue(second.json()['must_change_password'])
        self.assertIn(GATE, self.client().get('/subscription/usage', headers=self.H(second.json()['access_token'])).text)

    # ---------------------------------------------------------------- TMPPW-002
    def test_permanent_password_cannot_repeat_the_temporary_one(self):
        strong_temp = 'Temp-Pass-12'   # meets the full rule on purpose
        self.create_temp('staff', strong_temp)
        c = self.client()
        token = self.login(c, 't_staff', strong_temp, 'staff').json()['access_token']
        r = c.post('/auth/change-password', headers=self.H(token), json={'current_password': strong_temp, 'new_password': strong_temp})
        self.assertEqual(r.status_code, 400)
        self.assertIn('different from your temporary password', r.text)
        self.assertTrue(self.flag('t_staff'))
        self.assertIn(GATE, c.get('/products/', headers=self.H(token)).text)   # still gated, session intact
        ok = c.post('/auth/change-password', headers=self.H(token), json={'current_password': strong_temp, 'new_password': 'Perm-Pass-77'})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(self.login(self.client(), 't_staff', strong_temp, 'staff').status_code, 401)

    def test_failed_changes_keep_the_gate(self):
        pw = self.create_temp('staff')
        c = self.client()
        token = self.login(c, 't_staff', pw, 'staff').json()['access_token']
        self.assertEqual(c.post('/auth/change-password', headers=self.H(token), json={'current_password': pw, 'new_password': 'weak'}).status_code, 400)
        self.assertEqual(c.post('/auth/change-password', headers=self.H(token), json={'current_password': 'wrong-one', 'new_password': 'Perm-Pass-77'}).status_code, 400)
        self.assertEqual(self.client().post('/auth/change-password', json={'current_password': pw, 'new_password': 'Perm-Pass-77'}).status_code, 401)
        with patch.object(main, 'add_audit', side_effect=RuntimeError('simulated failure')):
            with self.assertRaises(RuntimeError):
                c.post('/auth/change-password', headers=self.H(token), json={'current_password': pw, 'new_password': 'Perm-Pass-77'})
        self.assertTrue(self.flag('t_staff'))
        self.assertEqual(self.login(self.client(), 't_staff', pw, 'staff').status_code, 200)

    def test_successful_change_releases_the_gate_and_revokes_temporary_sessions(self):
        pw = self.create_temp('manager')
        c, other = self.client(), self.client()
        token = self.login(c, 't_manager', pw, 'manager').json()['access_token']
        other_token = self.login(other, 't_manager', pw, 'manager').json()['access_token']
        ok = c.post('/auth/change-password', headers=self.H(token), json={'current_password': pw, 'new_password': 'Mgr-Perm-55'})
        self.assertEqual(ok.status_code, 200)
        self.assertFalse(self.flag('t_manager'))
        g = self.client()
        self.assertEqual(g.get('/products/', headers=self.H(ok.json()['access_token'])).status_code, 200)
        self.assertEqual(g.get('/business-profile/', headers=self.H(ok.json()['access_token'])).status_code, 200)
        self.assertEqual(g.get('/auth/me', headers=self.H(token)).status_code, 401)
        self.assertEqual(g.get('/auth/me', headers=self.H(other_token)).status_code, 401)
        self.assertEqual(other.post('/auth/refresh').status_code, 204)
        self.assertEqual(self.login(self.client(), 't_manager', pw, 'manager').status_code, 401)
        again = self.login(self.client(), 't_manager', 'Mgr-Perm-55', 'manager')
        self.assertEqual(again.status_code, 200)
        self.assertFalse(again.json()['must_change_password'])

    def test_admin_reset_rearms_the_gate_and_revokes_live_sessions(self):
        pw = self.create_temp('manager')
        c = self.client()
        token = self.login(c, 't_manager', pw, 'manager').json()['access_token']
        perm = c.post('/auth/change-password', headers=self.H(token), json={'current_password': pw, 'new_password': 'Mgr-Perm-55'}).json()['access_token']
        g = self.client()
        self.assertEqual(g.get('/products/', headers=self.H(perm)).status_code, 200)
        mid = self.db.query(main.User).filter_by(username='t_manager').one().id
        self.assertEqual(self.oc.patch(f'/users/{mid}/reset-password', headers=self.H(self.otok), json={'new_password': 'reset1x'}).status_code, 200)
        self.assertTrue(self.flag('t_manager'))
        self.assertEqual(g.get('/products/', headers=self.H(perm)).status_code, 401)
        self.assertEqual(c.post('/auth/refresh').status_code, 204)
        self.assertEqual(self.login(self.client(), 't_manager', 'Mgr-Perm-55', 'manager').status_code, 401)
        fresh = self.login(self.client(), 't_manager', 'reset1x', 'manager')
        self.assertTrue(fresh.json()['must_change_password'])
        self.assertIn(GATE, g.get('/subscription/usage', headers=self.H(fresh.json()['access_token'])).text)


if __name__ == '__main__':
    unittest.main()
