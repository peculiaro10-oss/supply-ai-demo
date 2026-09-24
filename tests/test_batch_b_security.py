"""Batch B — security and backend-correctness remediation (launch triage 10–21).

Endpoint tests on disposable SQLite with mocked email delivery, in the same
style as tests/test_price_list_upload.py. Roles: Admin, Manager, Staff
(default, granted and denied), guest; Tenant A vs Tenant B wherever the data is
tenant-owned. They execute the FastAPI handlers and SQLAlchemy persistence; they
do not prove PostgreSQL-specific behaviour or real Resend delivery (QA does).

  SEC-001  unknown Business IDs are rate limited, across every public route
  SEC-002  no [auth-diag] login diagnostics
  SEC-003  Forgot Password / reset answer identically for real and fake accounts
  SEC-004  /health/database publishes no database topology
  SEC-005  self-disable needs the current password
  X1       payment-method detail and payment history are Admin-only
  X3       uploaded files follow role/permission, not just tenant
  X2       inventory.view is enforced on the product/inventory reads
  OBS-7    the Ops console's markup and code need a platform-owner session
  OBS-13   Team Presence returns presence identity only
  SEC-NOTIFY-001/002  security emails after password and email changes
"""
import os
import io
import tempfile
import contextlib
os.environ['PYTHON_DOTENV_DISABLED'] = '1'
os.environ['SUPPLY_AI_SKIP_DB_STARTUP_CHECK'] = 'true'
os.environ['DATABASE_URL'] = 'postgresql+psycopg://test:test@127.0.0.1:65432/cauldra_test'
os.environ['SUPPLY_AI_SECRET_KEY'] = 'isolated-test-secret-012345678901234567890123456789'
os.environ.setdefault('SUPPLY_AI_UPLOAD_DIR', tempfile.mkdtemp(prefix='cauldra-batchb-'))
import json
import secrets
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient
import main

PASSWORD = 'Correct-Horse-1'
ROOT = Path(__file__).resolve().parents[1]


class BatchBBase(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        main.Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

        def db_dep():
            with self.Session() as session:
                yield session
        main.app.dependency_overrides[main.get_db] = db_dep
        self.db = self.Session()
        self.biz_a = self.business('AA-1111-11', 'Tenant A')
        self.biz_b = self.business('BB-2222-22', 'Tenant B')
        self.admin = self.user('owner', 'admin', self.biz_a)
        self.manager = self.user('manny', 'manager', self.biz_a)
        self.staff = self.user('stella', 'staff', self.biz_a)
        self.staff2 = self.user('sam', 'staff', self.biz_a)
        self.admin_b = self.user('bowner', 'admin', self.biz_b)
        self.sent = []   # (purpose, to, subject, html)

        def fake_send(*, purpose, to_email, subject, html):
            self.sent.append((purpose, to_email, subject, html))
        self.patches = [
            patch.object(main, 'send_resend_email', side_effect=fake_send),
            patch.object(main, 'RESEND_FROM', 'Cauldra <no-reply@example.com>'),
            patch.dict(os.environ, {'RESEND_API_KEY': 're_test_only'}),
            patch.object(main, 'background_session_factory', lambda: self.Session()),
        ]
        for p in self.patches:
            p.start()
        self.client = TestClient(main.app)

    def tearDown(self):
        self.client.close()
        main.app.dependency_overrides.clear()
        for p in self.patches:
            p.stop()
        self.db.close()
        self.engine.dispose()

    # ---------------------------------------------------------------- fixtures
    def business(self, code, name):
        biz = main.BusinessProfile(business_code=code, company_name=name, currency='NGN (₦)',
                                   subscription_plan='business', country_code='NG')
        self.db.add(biz); self.db.commit()
        now = datetime.utcnow()
        self.db.add(main.BusinessSubscription(business_id=biz.id, plan='business', billing_interval='monthly',
                                              status='active', current_period_start=now,
                                              current_period_end=now + timedelta(days=30), card_verified=True,
                                              card_last4='4081', card_type='visa', card_exp_month='12',
                                              card_exp_year='2030'))
        self.db.commit(); self.db.refresh(biz)
        return biz

    def user(self, username, role, biz, **extra):
        u = main.User(username=username, email=f'{username}@example.com', password=main.hash_password(PASSWORD),
                      phone='+2348030000000', role=role, business_id=biz.id, **extra)
        self.db.add(u); self.db.commit(); self.db.refresh(u)
        return u

    def auth(self, user):
        return {'Authorization': f'Bearer {main.issue_token(user, self.db)}'}

    def deny(self, user, *codes):
        self.db.refresh(user)
        user.permission_overrides = json.dumps({code: False for code in codes})
        self.db.commit()

    def grant(self, user, *codes):
        self.db.refresh(user)
        user.permission_overrides = json.dumps({code: True for code in codes})
        self.db.commit()


# ============================================================ SEC-001 / SEC-002
class BusinessLookupRateLimitTests(BatchBBase):
    def login(self, business_id, path='/auth/admin-login', **extra):
        body = {'business_id': business_id, 'username': 'owner', 'password': PASSWORD, **extra}
        return self.client.post(path, json=body)

    def test_unknown_business_ids_are_rate_limited(self):
        codes = [self.login(f'ZZ-0000-{i:02d}').status_code for i in range(main.RATE_LIMIT_MAX_FAILURES)]
        self.assertEqual(codes, [404] * main.RATE_LIMIT_MAX_FAILURES)
        blocked = self.login('ZZ-0000-99')
        self.assertEqual(blocked.status_code, 429, blocked.text)
        self.assertIn('Retry-After', blocked.headers)
        # The lookup never ran for the blocked request, and the budget is per
        # IP: a real business from this IP is refused too while it lasts.
        self.assertEqual(self.login('AA-1111-11').status_code, 429)

    def test_rotating_routes_shares_one_budget(self):
        routes = [
            lambda i: self.login(f'ZZ-1{i:03d}'),
            lambda i: self.login(f'ZZ-2{i:03d}', path='/auth/employee-login', selected_role='staff'),
            lambda i: self.client.post('/auth/verify-business', json={'business_id': f'ZZ-3{i:03d}'}),
            lambda i: self.client.post('/token', data={'username': 'owner', 'password': PASSWORD, 'scope': f'ZZ-4{i:03d}'}),
        ]
        statuses = [routes[i % 4](i).status_code for i in range(main.RATE_LIMIT_MAX_FAILURES)]
        self.assertEqual(statuses, [404] * main.RATE_LIMIT_MAX_FAILURES)
        for route in routes:
            self.assertEqual(route(99).status_code, 429)
        forgot = self.client.post('/auth/forgot-password', json={'business_id': 'ZZ-5555', 'username': 'x',
                                                                 'email': 'x@example.com', 'channel': 'email'})
        self.assertEqual(forgot.status_code, 429)

    def test_valid_login_still_works_and_wrong_password_is_401(self):
        self.assertEqual(self.login('AA-1111-11', password='nope').status_code, 401)
        ok = self.login('AA-1111-11')
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertIn('access_token', ok.json())

    def test_no_auth_diagnostic_output(self):
        source = (ROOT / 'backend' / 'main.py').read_text(encoding='utf-8')
        self.assertNotIn('auth-diag', source)
        self.assertNotIn('TEMPORARY DEV DIAGNOSTIC', source)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.login('AA-1111-11')
            self.login('AA-1111-11', password='wrong')
            self.login('ZZ-0000-00')
            self.client.post('/auth/refresh')
        self.assertNotIn('auth-diag', out.getvalue())
        self.assertNotIn('AA-1111-11', out.getvalue())


# ================================================================== SEC-003
class ForgotPasswordEnumerationTests(BatchBBase):
    def forgot(self, business_id='AA-1111-11', username='stella', email='stella@example.com'):
        return self.client.post('/auth/forgot-password', json={'business_id': business_id, 'username': username,
                                                               'email': email, 'channel': 'email'})

    def reset(self, recovery_id, code, password='Brand-New-Pass9'):
        return self.client.post('/auth/reset-password', json={'recovery_id': recovery_id, 'code': code,
                                                              'new_password': password})

    def code_from_last_email(self):
        purpose, _, _, body = self.sent[-1]
        self.assertEqual(purpose, 'password_recovery')
        return body.split('<strong>')[1].split('</strong>')[0]

    def test_all_combinations_answer_identically(self):
        cases = {
            'unknown business': self.forgot(business_id='ZZ-9999-99'),
            'unknown user': self.forgot(username='nobody'),
            'wrong email': self.forgot(email='someone.else@example.com'),
            'disabled account': None,
            'real account': self.forgot(),
        }
        self.db.refresh(self.staff2); self.staff2.disabled = True; self.db.commit()
        cases['disabled account'] = self.forgot(username='sam', email='sam@example.com')
        shapes = {}
        for name, r in cases.items():
            self.assertEqual(r.status_code, 200, (name, r.text))
            body = r.json()
            self.assertEqual(set(body), {'recovery_id', 'channel', 'expires_in_seconds', 'resend_after_seconds'})
            self.assertEqual(len(body['recovery_id']), len(cases['real account'].json()['recovery_id']))
            shapes[name] = {k: v for k, v in body.items() if k != 'recovery_id'}
        self.assertEqual(len({json.dumps(v, sort_keys=True) for v in shapes.values()}), 1, shapes)
        # Exactly one email, to the stored address of the real account.
        self.assertEqual([(p, to) for p, to, _, _ in self.sent], [('password_recovery', 'stella@example.com')])

    def test_decoy_and_wrong_code_get_the_same_reset_error(self):
        decoy = self.forgot(username='nobody').json()['recovery_id']
        real = self.forgot().json()['recovery_id']
        a = self.reset(decoy, '123456')
        b = self.reset(real, '000000' if self.code_from_last_email() != '000000' else '111111')
        self.assertEqual((a.status_code, a.json()), (b.status_code, b.json()))
        self.assertEqual(a.status_code, 400)

    def test_real_code_resets_and_sends_a_security_notice(self):
        real = self.forgot().json()['recovery_id']
        code = self.code_from_last_email()
        r = self.reset(real, code)
        self.assertEqual(r.status_code, 200, r.text)
        self.db.expire_all()
        self.assertTrue(main.verify_password('Brand-New-Pass9', self.db.get(main.User, self.staff.id).password))
        purpose, to, subject, body = self.sent[-1]
        self.assertEqual((purpose, to), ('security_password_changed', 'stella@example.com'))
        self.assertIn('password', subject.lower())
        for secret in ('Brand-New-Pass9', code, real, 'http'):
            self.assertNotIn(secret, body)

    def test_cooldown_answers_the_same_and_keeps_the_emailed_code_working(self):
        first = self.forgot().json()['recovery_id']
        code = self.code_from_last_email()
        second = self.forgot()
        self.assertEqual(second.status_code, 200)
        self.assertEqual(len(self.sent), 1, 'no second email inside the cooldown')
        second_id = second.json()['recovery_id']
        self.assertNotEqual(first, second_id)
        self.assertEqual(self.reset(first, code).status_code, 400, 'the older handle is retired')
        self.assertEqual(self.reset(second_id, code).status_code, 200)

    def test_every_request_counts_toward_the_limit_real_or_not(self):
        for i in range(main.RATE_LIMIT_MAX_FAILURES):
            self.assertEqual((self.forgot() if i % 2 else self.forgot(username='nobody')).status_code, 200)
        self.assertEqual(self.forgot().status_code, 429)
        self.assertEqual(self.forgot(username='nobody').status_code, 429)

    def test_delivery_failure_keeps_the_generic_answer_and_retires_the_code(self):
        main.send_resend_email.side_effect = main.EmailDeliveryError('provider_outage')
        r = self.forgot()
        self.assertEqual(r.status_code, 200, r.text)
        self.db.expire_all()
        row = self.db.query(main.PasswordRecovery).filter_by(recovery_id=r.json()['recovery_id']).one()
        self.assertTrue(row.used, 'a code that was never delivered must not block a retry')

    def test_unconfigured_provider_is_reported_before_any_lookup(self):
        with patch.dict(os.environ, {'RESEND_API_KEY': ''}):
            for r in (self.forgot(), self.forgot(business_id='ZZ-0000-00')):
                self.assertEqual(r.status_code, 503)
        self.assertEqual(self.sent, [])

    def test_malformed_requests(self):
        r = self.client.post('/auth/forgot-password', json={'business_id': 'AA-1111-11', 'username': 'stella', 'channel': 'fax'})
        self.assertEqual(r.status_code, 400)
        r = self.client.post('/auth/forgot-password', json={'business_id': 'AA-1111-11', 'username': 'stella', 'channel': 'email'})
        self.assertEqual(r.status_code, 400)
        r = self.client.post('/auth/forgot-password', json={'username': 'stella'})
        self.assertEqual(r.status_code, 422)


# ================================================================== SEC-004
class HealthDatabaseTests(BatchBBase):
    def test_public_response_has_no_topology(self):
        with patch.object(main, '_ping_database', return_value=None):
            r = self.client.get('/health/database')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {'status': 'ok', 'connected': True})
        for leak in ('host', 'port', 'database_name', 'schema', 'postgres_version', 'supabase', '5432'):
            self.assertNotIn(leak, r.text)

    def test_failure_is_503_without_detail(self):
        with patch.object(main, '_ping_database', side_effect=RuntimeError('db down at host x')):
            r = self.client.get('/health/database')
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.json(), {'status': 'degraded', 'connected': False})

    def test_liveness_endpoint_unchanged(self):
        with patch.object(main, '_ping_database', return_value=None):
            self.assertEqual(self.client.get('/health').json()['status'], 'ok')


# ================================================================== SEC-005
class SelfDisableTests(BatchBBase):
    def disable(self, user, body=None):
        return self.client.request('DELETE', '/auth/account', headers=self.auth(user), json=body)

    def is_disabled(self, user):
        self.db.expire_all()
        return self.db.get(main.User, user.id).disabled

    def test_requires_current_password(self):
        for u in (self.manager, self.staff):
            with self.subTest(role=u.role):
                self.assertEqual(self.disable(u).status_code, 400)
                self.assertEqual(self.disable(u, {}).status_code, 400)
                self.assertEqual(self.disable(u, {'password': 'definitely-wrong'}).status_code, 400)
                self.assertFalse(self.is_disabled(u))

    def test_correct_password_disables_and_revokes_sessions(self):
        headers = self.auth(self.staff)
        r = self.client.request('DELETE', '/auth/account', headers=headers, json={'password': PASSWORD})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(self.is_disabled(self.staff))
        self.assertIn(self.client.get('/auth/me', headers=headers).status_code, (401, 403))

    def test_owner_and_guest(self):
        self.assertEqual(self.disable(self.admin, {'password': PASSWORD}).status_code, 403)
        self.assertEqual(self.client.request('DELETE', '/auth/account', json={'password': PASSWORD}).status_code, 401)

    def test_wrong_passwords_are_rate_limited(self):
        for _ in range(main.RATE_LIMIT_MAX_FAILURES):
            self.assertEqual(self.disable(self.manager, {'password': 'wrong'}).status_code, 400)
        self.assertEqual(self.disable(self.manager, {'password': PASSWORD}).status_code, 429)
        self.assertFalse(self.is_disabled(self.manager))


# ======================================================================= X1
class BillingVisibilityTests(BatchBBase):
    def setUp(self):
        super().setUp()
        self.db.add_all([
            main.PaymentRecord(business_id=self.biz_a.id, plan='business', billing_interval='monthly', amount_kobo=2000000,
                               paystack_reference='ref-a-1', status='success', purpose='subscription'),
            main.PaymentRecord(business_id=self.biz_b.id, plan='business', billing_interval='monthly', amount_kobo=2000000,
                               paystack_reference='ref-b-1', status='success', purpose='subscription'),
        ]); self.db.commit()

    def test_admin_sees_payment_method_others_do_not(self):
        r = self.client.get('/subscription/usage', headers=self.auth(self.admin))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()['card_last4'], r.json()['card_exp_year']), ('4081', '2030'))
        self.assertTrue(r.json()['payment_details_visible'])
        for u in (self.manager, self.staff):
            with self.subTest(role=u.role):
                body = self.client.get('/subscription/usage', headers=self.auth(u)).json()
                for field in ('card_last4', 'card_type', 'card_exp_month', 'card_exp_year'):
                    self.assertIsNone(body[field])
                self.assertFalse(body['payment_details_visible'])
                self.assertNotIn('4081', json.dumps(body))
                # Plan, status and AI usage stay available.
                self.assertEqual(body['plan'], 'business')
                self.assertIn('included_ai_credits', body)

    def test_payment_history_is_admin_only_and_tenant_scoped(self):
        r = self.client.get('/subscription/payments', headers=self.auth(self.admin))
        self.assertEqual(r.status_code, 200)
        self.assertEqual([p['reference'] for p in r.json()], ['ref-a-1'])
        for u in (self.manager, self.staff):
            self.assertEqual(self.client.get('/subscription/payments', headers=self.auth(u)).status_code, 403)
        self.assertEqual(self.client.get('/subscription/payments').status_code, 401)
        r = self.client.get('/subscription/payments', headers=self.auth(self.admin_b))
        self.assertEqual([p['reference'] for p in r.json()], ['ref-b-1'])


# ======================================================================= X3
class UploadVisibilityTests(BatchBBase):
    def setUp(self):
        super().setUp()
        self.files = {}
        for name, kind, owner, biz in (
            ('staff_invoice', 'invoice', self.staff, self.biz_a),
            ('admin_invoice', 'invoice', self.admin, self.biz_a),
            ('manager_prices', 'price_list', self.manager, self.biz_a),
            ('staff2_avatar', 'avatar', self.staff2, self.biz_a),
            ('b_invoice', 'invoice', self.admin_b, self.biz_b),
        ):
            key = f'{name}-{secrets.token_hex(4)}.bin'
            (main.UPLOAD_STORAGE_DIR / key).write_bytes(b'content of ' + name.encode())
            row = main.StoredUpload(business_id=biz.id, uploaded_by_id=owner.id, kind=kind, original_name=f'{name}.png',
                                    storage_key=key, content_type='image/png', size_bytes=10, content_hash=name)
            self.db.add(row); self.db.commit(); self.db.refresh(row)
            self.files[name] = row.id

    def listed(self, user):
        r = self.client.get('/uploads', headers=self.auth(user))
        self.assertEqual(r.status_code, 200, r.text)
        ids = {row['id'] for row in r.json()}
        return {name for name, fid in self.files.items() if fid in ids}

    def download(self, user, name):
        return self.client.get(f'/uploads/{self.files[name]}/download', headers=self.auth(user)).status_code

    def test_list_by_role(self):
        self.assertEqual(self.listed(self.admin), {'staff_invoice', 'admin_invoice', 'manager_prices', 'staff2_avatar'})
        self.assertEqual(self.listed(self.manager), {'staff_invoice', 'admin_invoice', 'manager_prices'})
        self.assertEqual(self.listed(self.staff), {'staff_invoice'})
        self.assertEqual(self.listed(self.staff2), {'staff2_avatar'})
        self.assertEqual(self.listed(self.admin_b), {'b_invoice'})

    def test_grants_and_denials_follow_existing_permissions(self):
        self.grant(self.staff2, 'po.view')
        self.assertEqual(self.listed(self.staff2), {'staff2_avatar', 'staff_invoice', 'admin_invoice'})
        self.deny(self.manager, 'po.view', 'procurement.price_monitor')
        self.assertEqual(self.listed(self.manager), {'manager_prices'})

    def test_download_matches_the_list(self):
        self.assertEqual(self.download(self.staff, 'staff_invoice'), 200)
        self.assertEqual(self.download(self.staff, 'admin_invoice'), 404)
        self.assertEqual(self.download(self.staff, 'staff2_avatar'), 404)
        self.assertEqual(self.download(self.manager, 'admin_invoice'), 200)
        self.assertEqual(self.download(self.manager, 'staff2_avatar'), 404)
        self.assertEqual(self.download(self.admin, 'staff2_avatar'), 200)
        self.assertEqual(self.download(self.admin, 'b_invoice'), 404)      # tenant isolation
        self.assertEqual(self.download(self.admin_b, 'admin_invoice'), 404)
        self.assertEqual(self.client.get(f"/uploads/{self.files['staff_invoice']}/download").status_code, 401)
        self.assertEqual(self.client.get('/uploads').status_code, 401)


# ======================================================================= X2
class InventoryViewPermissionTests(BatchBBase):
    def setUp(self):
        super().setUp()
        for biz, name in ((self.biz_a, 'A rice'), (self.biz_b, 'B rice')):
            self.db.add(main.Product(name=name, sku=name, category='Grains', business_id=biz.id, cost_price=10.0,
                                     retail_price=12.0, quantity=5, min_stock_level=1, created_at=datetime.utcnow()))
        self.db.commit()
        self.product_a = self.db.query(main.Product).filter_by(name='A rice').one()

    def status(self, user, path):
        return self.client.get(path, headers=self.auth(user)).status_code

    def test_defaults_allow_every_role(self):
        for u in (self.admin, self.manager, self.staff):
            for path in ('/products/', '/products/inventory-summary', f'/products/{self.product_a.id}/warehouse-stocks'):
                with self.subTest(role=u.role, path=path):
                    self.assertEqual(self.status(u, path), 200)
        names = [p['name'] for p in self.client.get('/products/', headers=self.auth(self.staff)).json()]
        self.assertEqual(names, ['A rice'])

    def test_denying_inventory_view_closes_the_stock_views(self):
        self.deny(self.staff, 'inventory.view')
        self.assertEqual(self.status(self.staff, '/products/inventory-summary'), 403)
        self.assertEqual(self.status(self.staff, f'/products/{self.product_a.id}/warehouse-stocks'), 403)
        # The till still needs the catalogue it sells from (PERM-001's rule).
        self.assertEqual(self.status(self.staff, '/products/'), 200)

    def test_denying_both_closes_the_catalogue(self):
        self.deny(self.staff, 'inventory.view', 'sales.create')
        self.assertEqual(self.status(self.staff, '/products/'), 403)
        self.deny(self.manager, 'inventory.view', 'sales.create')
        self.assertEqual(self.status(self.manager, '/products/'), 403)
        self.assertEqual(self.client.get('/products/').status_code, 401)

    def test_other_tenant_product_is_unavailable(self):
        b_product = self.db.query(main.Product).filter_by(name='B rice').one()
        self.assertEqual(self.status(self.admin, f'/products/{b_product.id}/warehouse-stocks'), 404)


# ==================================================================== OBS-13
class TeamPresenceTests(BatchBBase):
    def test_presence_returns_identity_and_presence_only(self):
        r = self.client.get('/presence/team', headers=self.auth(self.admin))
        self.assertEqual(r.status_code, 200, r.text)
        employees = r.json()['employees']
        self.assertEqual({e['username'] for e in employees}, {'manny', 'stella', 'sam'})
        for e in employees:
            for hidden in ('must_change_password', 'auth_version', 'disabled', 'email_verified', 'pending_email', 'phone'):
                self.assertNotIn(hidden, e)
            for shown in ('id', 'username', 'firstname', 'lastname', 'role', 'position', 'email', 'status', 'online'):
                self.assertIn(shown, e)

    def test_permission_and_guest(self):
        self.assertEqual(self.client.get('/presence/team', headers=self.auth(self.manager)).status_code, 200)
        self.assertEqual(self.client.get('/presence/team', headers=self.auth(self.staff)).status_code, 403)
        self.assertEqual(self.client.get('/presence/team').status_code, 401)
        names = {e['username'] for e in self.client.get('/presence/team', headers=self.auth(self.admin_b)).json()['employees']}
        self.assertEqual(names, set())


# ===================================================================== OBS-7
class OpsConsoleTests(BatchBBase):
    def setUp(self):
        super().setUp()
        self.owner = main.PlatformOwner(email='ops@example.com', password=main.hash_password(PASSWORD),
                                        totp_enabled=True, totp_secret='JBSWY3DPEHPK3PXP')
        self.db.add(self.owner); self.db.commit(); self.db.refresh(self.owner)
        self.panel = main.PLATFORM_PANEL_PATH

    def test_only_sign_in_is_public(self):
        page = self.client.get(self.panel + '/')
        self.assertEqual(page.status_code, 200)
        self.assertIn('login-form-password', page.text)
        self.assertNotIn('app-shell', page.text)
        login_js = self.client.get(self.panel + '/login.js')
        self.assertEqual(login_js.status_code, 200)
        for endpoint in ('/api/platform/businesses', '/api/platform/revenue', '/api/platform/infrastructure'):
            self.assertNotIn(endpoint, login_js.text)
        for asset in ('/console.html', '/console.js', '/app.js'):
            self.assertEqual(self.client.get(self.panel + asset).status_code, 404, asset)

    def test_customer_tokens_and_forged_cookies_are_refused(self):
        customer = main.issue_token(self.admin, self.db)
        for cookie in (customer, 'garbage', ''):
            self.client.cookies.set(main.PLATFORM_PANEL_COOKIE, cookie)
            self.assertEqual(self.client.get(self.panel + '/console.js').status_code, 404)
        self.client.cookies.clear()

    def test_mfa_sets_a_scoped_cookie_that_unlocks_the_console(self):
        with patch.object(main, '_totp_verify', return_value=True):
            r = self.client.post('/api/platform/auth/verify-mfa',
                                 json={'mfa_token': main.issue_platform_mfa_token(self.owner), 'code': '123456'})
        self.assertEqual(r.status_code, 200, r.text)
        set_cookie = r.headers['set-cookie']
        for attr in (main.PLATFORM_PANEL_COOKIE + '=', 'HttpOnly', 'SameSite=strict', f'Path={self.panel}'):
            self.assertIn(attr.lower(), set_cookie.lower())
        self.client.cookies.set(main.PLATFORM_PANEL_COOKIE, r.json()['access_token'])
        js = self.client.get(self.panel + '/console.js')
        self.assertEqual(js.status_code, 200)
        self.assertIn('/api/platform/businesses', js.text)
        self.assertEqual(js.headers.get('cache-control'), 'no-store')
        self.assertEqual(self.client.get(self.panel + '/console.html').status_code, 200)
        # Signing out revokes the token, so the cookie no longer opens anything.
        out = self.client.post('/api/platform/auth/logout', headers={'Authorization': f"Bearer {r.json()['access_token']}"})
        self.assertEqual(out.status_code, 200)
        self.client.cookies.set(main.PLATFORM_PANEL_COOKIE, r.json()['access_token'])
        self.assertEqual(self.client.get(self.panel + '/console.js').status_code, 404)
        self.client.cookies.clear()

    def test_platform_routes_are_not_in_openapi(self):
        paths = self.client.get('/openapi.json').json()['paths']
        self.assertFalse([p for p in paths if p.startswith('/api/platform') or p.startswith(self.panel)])
        self.assertNotIn('/health/database', paths)

    def test_platform_api_still_needs_its_own_token(self):
        self.assertEqual(self.client.get('/api/platform/overview').status_code, 401)
        customer = main.issue_token(self.admin, self.db)
        self.assertIn(self.client.get('/api/platform/overview', headers={'Authorization': f'Bearer {customer}'}).status_code, (401, 403))


# ======================================================== SEC-NOTIFY-001 / 002
class SecurityNotificationTests(BatchBBase):
    def test_password_change_sends_one_notice_without_secrets(self):
        r = self.client.post('/auth/change-password', headers=self.auth(self.staff),
                             json={'current_password': PASSWORD, 'new_password': 'Another-Pass77'})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual([(p, to) for p, to, _, _ in self.sent], [('security_password_changed', 'stella@example.com')])
        body = self.sent[0][3]
        for secret in ('Another-Pass77', PASSWORD, 'http'):
            self.assertNotIn(secret, body)

    def test_rejected_change_sends_nothing(self):
        r = self.client.post('/auth/change-password', headers=self.auth(self.staff),
                             json={'current_password': 'wrong', 'new_password': 'Another-Pass77'})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.sent, [])

    def test_admin_reset_notifies_the_employee_without_the_temporary_password(self):
        r = self.client.patch(f'/users/{self.staff.id}/reset-password', headers=self.auth(self.admin),
                              json={'new_password': 'TempPass#2026'})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual([(p, to) for p, to, _, _ in self.sent], [('security_password_changed', 'stella@example.com')])
        self.assertNotIn('TempPass#2026', self.sent[0][3])
        self.assertIn('administrator', self.sent[0][3])

    def test_email_change_notifies_old_and_new_addresses(self):
        self.db.refresh(self.manager); self.manager.pending_email = 'manny.new@example.org'; self.db.commit()
        with patch.object(main, '_supabase_email_confirmed', return_value=True):
            r = self.client.post('/users/me/email-change/confirm', headers=self.auth(self.manager))
        self.assertEqual(r.status_code, 200, r.text)
        sent = {(p, to): body for p, to, _, body in self.sent}
        self.assertEqual(set(sent), {('security_email_changed_old', 'manny@example.com'),
                                     ('security_email_changed_new', 'manny.new@example.org')})
        old_body = sent[('security_email_changed_old', 'manny@example.com')]
        self.assertIn('m***@example.org', old_body)
        self.assertNotIn('manny.new@example.org', old_body)

    def test_unconfirmed_email_change_sends_nothing(self):
        self.db.refresh(self.manager); self.manager.pending_email = 'manny.new@example.org'; self.db.commit()
        with patch.object(main, '_supabase_email_confirmed', return_value=False):
            r = self.client.post('/users/me/email-change/confirm', headers=self.auth(self.manager))
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self.sent, [])

    def test_delivery_failure_never_fails_the_change(self):
        main.send_resend_email.side_effect = main.EmailDeliveryError('provider_outage')
        r = self.client.post('/auth/change-password', headers=self.auth(self.staff),
                             json={'current_password': PASSWORD, 'new_password': 'Another-Pass77'})
        self.assertEqual(r.status_code, 200, r.text)


if __name__ == '__main__':
    unittest.main()
