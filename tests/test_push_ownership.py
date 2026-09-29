"""NOTIF-PUSH-001 / NOTIF-PUSH-002 / NOTIF-PUSH-004 — push device ownership.

A browser (Web Push) or Android app (FCM) registration belongs to the sign-in
that made it. It must stop receiving a user's pushes at sign-out, session
revocation, password change or reset, account disable or deletion, and it must
move — never linger — when another user of the same or another business
registers the same device. Push failures never touch the in-app row, and a
push is sent only after the transaction that wrote the notification commits.

Each TestClient is one physical device (its own cookie jar). Web Push and FCM
sends are recorded, never sent. Disposable SQLite + TestClient, in the style of
tests/test_temp_password_gate.py.
"""
import os
import tempfile
os.environ['PYTHON_DOTENV_DISABLED'] = '1'
os.environ['SUPPLY_AI_SKIP_DB_STARTUP_CHECK'] = 'true'
os.environ['DATABASE_URL'] = 'postgresql+psycopg://test:test@127.0.0.1:65432/cauldra_test'
os.environ['SUPPLY_AI_SECRET_KEY'] = 'isolated-test-secret-012345678901234567890123456789'
os.environ.setdefault('SUPPLY_AI_UPLOAD_DIR', tempfile.mkdtemp(prefix='cauldra-push-'))
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient
from pywebpush import WebPushException
import main

PW = 'Strong-Pass-1'
ENDPOINT = 'https://fcm.googleapis.com/fcm/send/device-one-web'
ENDPOINT_2 = 'https://fcm.googleapis.com/fcm/send/device-two-web'
TOKEN = 'native-token-device-one-' + 'x' * 40
TOKEN_2 = 'native-token-device-two-' + 'y' * 40
KEYS = {'p256dh': 'BPublicKeyForTests', 'auth': 'authsecret'}


class PushOwnership(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        main.Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

        def db_dep():
            with self.Session() as session:
                yield session
        main.app.dependency_overrides[main.get_db] = db_dep
        self.db = self.Session()
        self.web_sent, self.fcm_sent = [], []
        self.web_behaviour, self.fcm_behaviour = {}, {}

        def fake_webpush(subscription_info, **kwargs):
            endpoint = subscription_info['endpoint']
            behaviour = self.web_behaviour.get(endpoint)
            if behaviour == 410:
                raise WebPushException('gone', response=SimpleNamespace(status_code=410))
            if behaviour == 'boom':
                raise RuntimeError('provider down')
            self.web_sent.append(endpoint)

        def fake_fcm_send(token, notification):
            behaviour = self.fcm_behaviour.get(token)
            if behaviour == 'raise':
                raise RuntimeError('fcm down')
            if behaviour in ('gone', 'failed'):
                return behaviour
            self.fcm_sent.append((token, notification.title))
            return 'sent'

        self.patches = [
            patch.object(main, 'send_resend_email', lambda **k: None),
            patch.object(main, 'RESEND_FROM', 'Cauldra <no-reply@example.com>'),
            patch.dict(os.environ, {'RESEND_API_KEY': 're_test_only'}),
            patch.object(main, 'background_session_factory', lambda: self.Session()),
            patch.object(main, 'PAYSTACK_SECRET_KEY', ''),
            patch.object(main, 'VAPID_PUBLIC_KEY', 'test-public'),
            patch.object(main, 'VAPID_PRIVATE_KEY', 'test-private'),
            patch.object(main, 'FCM_SERVICE_ACCOUNT_JSON', '{"project_id": "test"}'),
            patch.object(main, 'webpush', fake_webpush),
            patch.object(main, 'fcm_send', fake_fcm_send),
        ]
        for p in self.patches:
            p.start()
        self.biz_a = self.make_business('PA-1111-11', 'Push A')
        self.biz_b = self.make_business('PB-2222-22', 'Push B')
        self.admin_a = self.make_user(self.biz_a, 'admin_a', 'admin')
        self.manager_a = self.make_user(self.biz_a, 'manager_a', 'manager')
        self.staff_a = self.make_user(self.biz_a, 'staff_a', 'staff')
        self.admin_b = self.make_user(self.biz_b, 'admin_b', 'admin')

    def tearDown(self):
        main.app.dependency_overrides.clear()
        for p in self.patches:
            p.stop()
        self.db.close()
        self.engine.dispose()

    # ---------------------------------------------------------------- helpers
    def make_business(self, code, name):
        now = datetime.utcnow()
        biz = main.BusinessProfile(business_code=code, company_name=name, currency='NGN (₦)', subscription_plan='business', country_code='NG')
        self.db.add(biz); self.db.commit()
        self.db.add(main.BusinessSubscription(business_id=biz.id, plan='business', billing_interval='monthly', status='active',
                                              current_period_start=now, current_period_end=now + timedelta(days=30), card_verified=True))
        self.db.commit()
        return biz

    def make_user(self, biz, username, role):
        u = main.User(username=username, email=f'{username}@example.com', password=main.hash_password(PW), phone='+2348030000000',
                      role=role, business_id=biz.id, must_change_password=False, auth_version=1)
        self.db.add(u); self.db.commit()
        return u

    @staticmethod
    def device():
        return TestClient(main.app, base_url='https://testserver')

    @staticmethod
    def H(token):
        return {'Authorization': f'Bearer {token}'}

    def sign_in(self, device, user):
        biz = self.db.get(main.BusinessProfile, user.business_id)
        if user.role == 'admin':
            r = device.post('/auth/admin-login', json={'business_id': biz.business_code, 'username': user.username, 'password': PW})
        else:
            r = device.post('/auth/employee-login', json={'business_id': biz.business_code, 'username': user.username, 'password': PW,
                                                          'selected_role': user.role})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()['access_token']

    def register(self, device, token, web=True, native=True, endpoint=ENDPOINT, fcm_token=TOKEN):
        if web:
            r = device.post('/push/subscribe', headers=self.H(token), json={'endpoint': endpoint, 'keys': KEYS})
            self.assertEqual(r.status_code, 200, r.text)
        if native:
            r = device.post('/push/native/register', headers=self.H(token), json={'token': fcm_token, 'platform': 'android'})
            self.assertEqual(r.status_code, 200, r.text)

    def notify(self, user, dedup_key=None, commit=True):
        """A push-eligible (critical) notification for one user, written and
        committed on its own session; returns what the devices received."""
        self.web_sent.clear(); self.fcm_sent.clear()
        with self.Session() as s:
            rows = main.create_notification(s, business_id=user.business_id, category='security', severity='critical', type='TEST_ALERT',
                                            title=f'Alert for {user.username}', message='Something needs attention.',
                                            recipient_user_ids={user.id}, dedup_key=dedup_key)
            ids = [n.id for n in rows]
            if commit:
                s.commit()
            else:
                s.rollback()
        return {'web': list(self.web_sent), 'native': [t for t, _ in self.fcm_sent], 'ids': ids}

    def reached(self, user):
        got = self.notify(user)
        return got['web'] + got['native']

    def rows(self):
        self.db.expire_all()
        return (self.db.query(main.PushSubscription).all(), self.db.query(main.NativePushDevice).all())

    # ---------------------------------------------------------------- baseline
    def test_signed_in_device_receives_both_channels(self):
        d = self.device()
        self.register(d, self.sign_in(d, self.admin_a))
        self.assertEqual(self.reached(self.admin_a), [ENDPOINT, TOKEN])
        web, native = self.rows()
        self.assertEqual((web[0].user_id, web[0].business_id), (self.admin_a.id, self.biz_a.id))
        self.assertEqual((native[0].user_id, native[0].business_id, native[0].platform), (self.admin_a.id, self.biz_a.id, 'android'))

    def test_registration_needs_a_live_sign_in_on_that_device(self):
        d = self.device()
        token = self.sign_in(d, self.admin_a)
        bare = self.device()  # the access token alone, no sign-in cookie on this device
        for path, body in (('/push/subscribe', {'endpoint': ENDPOINT, 'keys': KEYS}), ('/push/native/register', {'token': TOKEN})):
            r = bare.post(path, headers=self.H(token), json=body)
            self.assertEqual(r.status_code, 409, r.text)
            self.assertEqual(r.json()['detail'], main.PUSH_SIGN_IN_REQUIRED_DETAIL)
        self.assertEqual(self.rows(), ([], []))

    def test_only_android_can_register_native(self):
        d = self.device()
        r = d.post('/push/native/register', headers=self.H(self.sign_in(d, self.admin_a)), json={'token': TOKEN, 'platform': 'ios'})
        self.assertEqual(r.status_code, 400)

    def test_refresh_rotation_keeps_the_registration(self):
        d = self.device()
        self.register(d, self.sign_in(d, self.admin_a))
        for _ in range(3):
            self.assertEqual(d.post('/auth/refresh').status_code, 200)
        self.assertEqual(self.reached(self.admin_a), [ENDPOINT, TOKEN])

    # ---------------------------------------------------------------- C. logout, then nobody
    def test_logout_with_device_identity_stops_delivery(self):
        d = self.device()
        token = self.sign_in(d, self.admin_a)
        self.register(d, token)
        r = d.post('/auth/logout', headers=self.H(token), json={'push_endpoint': ENDPOINT, 'native_push_token': TOKEN})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.reached(self.admin_a), [])
        web, native = self.rows()
        self.assertEqual({w.revoked_reason for w in web} | {n.revoked_reason for n in native}, {'signed_out'})

    def test_logout_without_any_client_cleanup_still_stops_delivery(self):
        d = self.device()
        token = self.sign_in(d, self.admin_a)
        self.register(d, token)
        self.assertEqual(d.post('/auth/logout', headers=self.H(token)).status_code, 200)  # no body at all
        self.assertEqual(self.reached(self.admin_a), [])

    def test_logout_on_one_device_keeps_the_users_other_devices(self):
        phone, laptop = self.device(), self.device()
        t1 = self.sign_in(phone, self.admin_a); self.register(phone, t1)
        t2 = self.sign_in(laptop, self.admin_a); self.register(laptop, t2, endpoint=ENDPOINT_2, fcm_token=TOKEN_2)
        phone.post('/auth/logout', headers=self.H(t1))
        self.assertEqual(sorted(self.reached(self.admin_a)), sorted([ENDPOINT_2, TOKEN_2]))

    # ---------------------------------------------------------------- A. same business, different user
    def test_same_business_user_switch_moves_the_device(self):
        d = self.device()
        t_admin = self.sign_in(d, self.admin_a); self.register(d, t_admin)
        d.post('/auth/logout', headers=self.H(t_admin))
        t_mgr = self.sign_in(d, self.manager_a)
        self.assertEqual(self.reached(self.admin_a), [])        # before the manager registers
        self.register(d, t_mgr)
        self.assertEqual(self.reached(self.admin_a), [])        # after
        self.assertEqual(self.reached(self.manager_a), [ENDPOINT, TOKEN])
        web, native = self.rows()
        self.assertEqual((len(web), len(native)), (1, 1))       # moved, never duplicated
        self.assertEqual((web[0].user_id, native[0].user_id), (self.manager_a.id, self.manager_a.id))

    # ---------------------------------------------------------------- B. different business
    def test_other_business_user_switch_moves_the_device(self):
        d = self.device()
        t_a = self.sign_in(d, self.admin_a); self.register(d, t_a)
        d.post('/auth/logout', headers=self.H(t_a))
        t_b = self.sign_in(d, self.admin_b)
        self.assertEqual(self.reached(self.admin_a), [])
        self.register(d, t_b)
        self.assertEqual(self.reached(self.admin_a), [])
        self.assertEqual(self.reached(self.admin_b), [ENDPOINT, TOKEN])
        web, native = self.rows()
        self.assertEqual((web[0].business_id, native[0].business_id), (self.biz_b.id, self.biz_b.id))

    def test_other_business_sign_in_without_sign_out_takes_the_device(self):
        d = self.device()
        self.register(d, self.sign_in(d, self.admin_a))
        self.register(d, self.sign_in(d, self.admin_b))  # the new sign-in's cookie replaces A's on this device
        self.assertEqual(self.reached(self.admin_a), [])
        self.assertEqual(self.reached(self.admin_b), [ENDPOINT, TOKEN])

    # ---------------------------------------------------------------- D. disable / delete
    def test_disabled_account_stops_delivery_and_reenable_needs_a_new_sign_in(self):
        d, owner = self.device(), self.device()
        self.register(d, self.sign_in(d, self.manager_a))
        t_owner = self.sign_in(owner, self.admin_a)
        self.assertEqual(owner.patch(f'/users/{self.manager_a.id}/disable', headers=self.H(t_owner)).status_code, 200)
        self.assertEqual(self.reached(self.manager_a), [])
        web, native = self.rows()
        self.assertEqual({web[0].revoked_reason, native[0].revoked_reason}, {'sessions_revoked'})
        self.assertEqual(owner.patch(f'/users/{self.manager_a.id}/enable', headers=self.H(t_owner)).status_code, 200)
        self.assertEqual(self.reached(self.manager_a), [])       # the old registration never comes back by itself

    def test_deleted_account_stops_delivery(self):
        d, owner = self.device(), self.device()
        self.register(d, self.sign_in(d, self.staff_a))
        t_owner = self.sign_in(owner, self.admin_a)
        staff_id = self.staff_a.id
        self.assertEqual(owner.delete(f'/users/{staff_id}', headers=self.H(t_owner)).status_code, 200)
        self.db.expire_all()
        self.assertEqual(self.db.query(main.PushSubscription).filter(main.PushSubscription.user_id == staff_id,
                                                                     main.PushSubscription.revoked_at.is_(None)).count(), 0)
        self.assertEqual(self.db.query(main.NativePushDevice).filter(main.NativePushDevice.user_id == staff_id,
                                                                     main.NativePushDevice.revoked_at.is_(None)).count(), 0)

    # ---------------------------------------------------------------- E. password change / Admin reset
    def test_own_password_change_revokes_then_reregistration_restores(self):
        d = self.device()
        token = self.sign_in(d, self.manager_a)
        self.register(d, token)
        r = d.post('/auth/change-password', headers=self.H(token), json={'current_password': PW, 'new_password': 'Newer-Pass-22'})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.reached(self.manager_a), [])
        self.register(d, r.json()['access_token'])              # the app re-registers after the change
        self.assertEqual(self.reached(self.manager_a), [ENDPOINT, TOKEN])

    def test_admin_reset_revokes_the_employees_devices(self):
        d, owner = self.device(), self.device()
        self.register(d, self.sign_in(d, self.staff_a))
        t_owner = self.sign_in(owner, self.admin_a)
        r = owner.patch(f'/users/{self.staff_a.id}/reset-password', headers=self.H(t_owner), json={'new_password': 'TempReset9x'})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.reached(self.staff_a), [])

    def test_temporary_password_account_cannot_register(self):
        d, owner = self.device(), self.device()
        t_owner = self.sign_in(owner, self.admin_a)
        owner.patch(f'/users/{self.staff_a.id}/reset-password', headers=self.H(t_owner), json={'new_password': 'TempReset9x'})
        r = d.post('/auth/employee-login', json={'business_id': 'PA-1111-11', 'username': 'staff_a', 'password': 'TempReset9x', 'selected_role': 'staff'})
        token = r.json()['access_token']
        r = d.post('/push/native/register', headers=self.H(token), json={'token': TOKEN})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()['detail'], main.TEMP_PASSWORD_GATE_DETAIL)

    def test_expired_sign_in_stops_delivery(self):
        d = self.device()
        self.register(d, self.sign_in(d, self.admin_a))
        self.db.query(main.RefreshSession).update({main.RefreshSession.expires_at: datetime.utcnow() - timedelta(minutes=1)})
        self.db.commit()
        self.assertEqual(self.reached(self.admin_a), [])

    # ---------------------------------------------------------------- F. stale / failing registrations
    def test_gone_registrations_are_disabled_and_the_in_app_row_stands(self):
        phone, laptop = self.device(), self.device()
        self.register(phone, self.sign_in(phone, self.admin_a))
        self.register(laptop, self.sign_in(laptop, self.admin_a), endpoint=ENDPOINT_2, fcm_token=TOKEN_2)
        self.web_behaviour[ENDPOINT] = 410
        self.fcm_behaviour[TOKEN] = 'gone'
        got = self.notify(self.admin_a)
        self.assertEqual((got['web'], got['native']), ([ENDPOINT_2], [TOKEN_2]))   # the healthy device still gets it
        web, native = self.rows()
        disabled = {w.endpoint for w in web if w.disabled_at} | {n.token for n in native if n.disabled_at}
        self.assertEqual(disabled, {ENDPOINT, TOKEN})
        n = self.db.get(main.Notification, got['ids'][0])
        self.assertFalse(n.is_read)
        self.assertIsNotNone(n.push_sent_at)
        self.assertEqual(self.reached(self.admin_a), [ENDPOINT_2, TOKEN_2])          # never retried on the gone ones

    def test_provider_failure_never_touches_the_in_app_notification(self):
        d = self.device()
        self.register(d, self.sign_in(d, self.admin_a))
        self.web_behaviour[ENDPOINT] = 'boom'
        self.fcm_behaviour[TOKEN] = 'raise'
        got = self.notify(self.admin_a)
        self.assertEqual((got['web'], got['native']), ([], []))
        self.db.expire_all()
        n = self.db.get(main.Notification, got['ids'][0])
        self.assertIsNotNone(n)
        self.assertFalse(n.is_read)
        self.assertIsNone(n.push_sent_at)
        web, native = self.rows()
        self.assertIsNone(web[0].disabled_at); self.assertIsNone(native[0].disabled_at)  # transient: kept for the next event
        self.fcm_behaviour.clear(); self.web_behaviour.clear()
        self.assertEqual(self.reached(self.admin_a), [ENDPOINT, TOKEN])

    def test_transient_fcm_failure_keeps_the_token(self):
        d = self.device()
        self.register(d, self.sign_in(d, self.admin_a), web=False)
        self.fcm_behaviour[TOKEN] = 'failed'
        self.notify(self.admin_a)
        self.assertIsNone(self.rows()[1][0].disabled_at)

    def test_legacy_unbound_subscription_is_never_pushed(self):
        self.db.add(main.PushSubscription(business_id=self.biz_a.id, user_id=self.admin_a.id, endpoint=ENDPOINT, p256dh='k', auth='a'))
        self.db.commit()
        self.assertEqual(self.reached(self.admin_a), [])
        self.assertEqual(self.rows()[0][0].revoked_reason, 'session_inactive')

    # ---------------------------------------------------------------- wrong user / wrong business dispatch
    def test_a_registration_is_only_ever_used_for_its_own_user_and_business(self):
        d = self.device()
        self.register(d, self.sign_in(d, self.admin_a))
        self.assertEqual(self.reached(self.manager_a), [])
        self.assertEqual(self.reached(self.admin_b), [])
        # A row that somehow points at another business than its user never delivers.
        self.db.query(main.NativePushDevice).update({main.NativePushDevice.business_id: self.biz_b.id}); self.db.commit()
        self.assertEqual(self.reached(self.admin_a), [ENDPOINT])

    # ---------------------------------------------------------------- G. duplicates / dedup
    def test_duplicate_registration_is_one_row_and_one_push(self):
        d = self.device()
        token = self.sign_in(d, self.admin_a)
        for _ in range(3):
            self.register(d, token)
        web, native = self.rows()
        self.assertEqual((len(web), len(native)), (1, 1))
        self.assertEqual(self.reached(self.admin_a), [ENDPOINT, TOKEN])

    def test_dedup_key_means_one_row_and_one_push(self):
        d = self.device()
        self.register(d, self.sign_in(d, self.admin_a))
        first = self.notify(self.admin_a, dedup_key='same-condition')
        second = self.notify(self.admin_a, dedup_key='same-condition')
        self.assertEqual((len(first['ids']), first['web'], first['native']), (1, [ENDPOINT], [TOKEN]))
        self.assertEqual((second['ids'], second['web'], second['native']), ([], [], []))

    # ---------------------------------------------------------------- NOTIF-PUSH-004 transaction order
    def test_a_rolled_back_notification_is_never_pushed(self):
        d = self.device()
        self.register(d, self.sign_in(d, self.admin_a))
        got = self.notify(self.admin_a, commit=False)
        self.assertEqual((got['web'], got['native']), ([], []))
        self.db.expire_all()
        self.assertEqual(self.db.query(main.Notification).count(), 0)
        # A later commit of other work on the SAME session does not replay it.
        with self.Session() as s:
            main.create_notification(s, business_id=self.biz_a.id, category='security', severity='critical', type='TEST_ALERT',
                                     title='t', message='m', recipient_user_ids={self.admin_a.id})
            s.rollback()
            s.get(main.User, self.admin_a.id).firstname = 'Renamed'
            s.commit()
        self.assertEqual((self.web_sent, self.fcm_sent), ([], []))

    def test_push_waits_for_the_commit(self):
        d = self.device()
        self.register(d, self.sign_in(d, self.admin_a))
        self.web_sent.clear(); self.fcm_sent.clear()
        with self.Session() as s:
            main.create_notification(s, business_id=self.biz_a.id, category='security', severity='critical', type='TEST_ALERT',
                                     title='t', message='m', recipient_user_ids={self.admin_a.id})
            self.assertEqual((self.web_sent, self.fcm_sent), ([], []))   # flushed, not yet committed
            s.commit()
        self.assertEqual((self.web_sent, [t for t, _ in self.fcm_sent]), ([ENDPOINT], [TOKEN]))

    def test_important_and_info_are_never_pushed(self):
        d = self.device()
        self.register(d, self.sign_in(d, self.admin_a))
        self.web_sent.clear(); self.fcm_sent.clear()
        with self.Session() as s:
            for severity in ('important', 'info'):
                main.create_notification(s, business_id=self.biz_a.id, category='security', severity=severity, type='TEST',
                                         title='t', message='m', recipient_user_ids={self.admin_a.id})
            s.commit()
        self.assertEqual((self.web_sent, self.fcm_sent), ([], []))

    def test_live_stockout_through_the_api_pushes_after_commit(self):
        d = self.device()
        token = self.sign_in(d, self.admin_a)
        self.register(d, token)
        wh = main.Warehouse(business_id=self.biz_a.id, name='Main Central Warehouse', is_active=True)
        self.db.add(wh); self.db.commit()
        p = main.Product(sku='A', name='Item A', category='QA', quantity=1, min_stock_level=0, cost_price=1, retail_price=2,
                         warehouse='Main Central Warehouse', warehouse_id=wh.id, business_id=self.biz_a.id)
        # A second, unsold product makes item A a high seller (stockout -> critical).
        self.db.add(main.Product(sku='B', name='Item B', category='QA', quantity=50, min_stock_level=0, cost_price=1, retail_price=2,
                                 warehouse='Main Central Warehouse', warehouse_id=wh.id, business_id=self.biz_a.id))
        self.db.add(p); self.db.commit()
        self.db.add(main.WarehouseStock(business_id=self.biz_a.id, product_id=p.id, warehouse='Main Central Warehouse', warehouse_id=wh.id, quantity=1))
        self.db.add(main.SaleModel(business_id=self.biz_a.id, product_id=p.id, quantity=5, total_price=10, timestamp=datetime.utcnow()))
        self.db.commit()
        self.web_sent.clear(); self.fcm_sent.clear()
        r = d.patch(f'/products/{p.id}/stock', headers=self.H(token), json={'quantity_change': -1})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.web_sent, [ENDPOINT])
        self.assertEqual(self.fcm_sent, [(TOKEN, 'Critical stockout')])


class FcmSend(unittest.TestCase):
    """fcm_send's classification of FCM HTTP v1 answers (no network)."""
    def run_with(self, status, body):
        n = SimpleNamespace(id=7, title='Critical stockout', message='Item A is now out of stock.', deep_link='inventory:1',
                            category='inventory', severity='critical')
        sent = {}

        class Resp:
            status_code = status
            def json(self):
                return body

        def fake_post(url, json=None, headers=None, timeout=None):
            sent.update(url=url, json=json)
            return Resp()
        with patch.object(main, '_fcm_access', lambda: ('proj-1', 'access')), patch('requests.post', fake_post):
            return main.fcm_send('tok', n), sent

    def test_success_and_message_shape(self):
        outcome, sent = self.run_with(200, {'name': 'projects/proj-1/messages/1'})
        self.assertEqual(outcome, 'sent')
        self.assertEqual(sent['url'], 'https://fcm.googleapis.com/v1/projects/proj-1/messages:send')
        msg = sent['json']['message']
        self.assertEqual(msg['token'], 'tok')
        self.assertEqual(msg['notification'], {'title': 'Critical stockout', 'body': 'Item A is now out of stock.'})
        self.assertEqual(msg['data'], {'notification_id': '7', 'deep_link': 'inventory:1', 'category': 'inventory', 'severity': 'critical'})
        self.assertEqual(msg['android']['notification']['channel_id'], 'cauldra_alerts')
        self.assertEqual(msg['android']['notification']['visibility'], 'PRIVATE')
        self.assertEqual(msg['android']['notification']['tag'], 'cauldra-notification-7')
        self.assertEqual(msg['android']['notification']['icon'], 'ic_stat_cauldra')
        self.assertEqual(msg['android']['notification']['color'], '#0B1B3F')

    def test_unregistered_and_mismatch_are_gone(self):
        for status, code in ((404, 'UNREGISTERED'), (403, 'SENDER_ID_MISMATCH')):
            body = {'error': {'status': 'NOT_FOUND', 'details': [{'@type': 'type.googleapis.com/google.firebase.fcm.v1.FcmError', 'errorCode': code}]}}
            self.assertEqual(self.run_with(status, body)[0], 'gone')
        body = {'error': {'status': 'INVALID_ARGUMENT', 'message': 'The registration token is not a valid FCM registration token'}}
        self.assertEqual(self.run_with(400, body)[0], 'gone')

    def test_service_account_with_a_byte_order_mark_is_accepted(self):
        import subprocess, sys
        code = ("import os; os.environ['FCM_SERVICE_ACCOUNT_JSON'] = '\\ufeff{\"project_id\": \"p\"}\\n'; import main, json; "
                "print(json.loads(main.FCM_SERVICE_ACCOUNT_JSON)['project_id'])")
        env = {**os.environ, 'PYTHONPATH': os.pathsep.join([os.path.join(os.path.dirname(__file__), '..', 'backend'), os.environ.get('PYTHONPATH', '')])}
        out = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, env=env, timeout=300)
        self.assertEqual(out.stdout.strip().splitlines()[-1], 'p', out.stderr[-500:])

    def test_server_errors_are_transient(self):
        for status in (429, 500, 503):
            self.assertEqual(self.run_with(status, {'error': {'status': 'UNAVAILABLE'}})[0], 'failed')


if __name__ == '__main__':
    unittest.main()
