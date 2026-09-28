"""SUB-LIFECYCLE-001 — paid subscriptions pause at the exact paid-through time.

Owner policy (2026-09-28): no ordinary grace period; data is never touched by
expiry; Billing stays reachable so an Admin can pay; staff get a clear paused
message; reminders 7d / 3d / 24h / 6h / 1h before the exact expiry plus a notice
at the pause; Paystack confirmation is the only thing that restores access.

Disposable SQLite; real endpoints; Paystack and email are simulated (no network).
"""
import os
os.environ['PYTHON_DOTENV_DISABLED'] = '1'
os.environ['SUPPLY_AI_SKIP_DB_STARTUP_CHECK'] = 'true'
os.environ['DATABASE_URL'] = 'postgresql+psycopg://test:test@127.0.0.1:65432/cauldra_test'
os.environ['SUPPLY_AI_SECRET_KEY'] = 'isolated-test-secret-012345678901234567890123456789'
import hashlib
import hmac
import json
import unittest
import uuid
from datetime import datetime, timedelta
from unittest.mock import patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient
import main

HOUR = timedelta(hours=1)
DAY = timedelta(days=1)
PAYSTACK_TEST_SECRET = 'sk_test_simulated_lifecycle_only'


def fake_initialize(method, path, body=None, timeout=15):
    assert path == '/transaction/initialize', path
    return {'data': {'access_code': 'AC_SIM', 'authorization_url': 'https://checkout.paystack.com/AC_SIM',
                     'reference': body['reference']}}


class SubscriptionLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        main.Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

        def db_dep():
            with self.Session() as session:
                yield session
        main.app.dependency_overrides[main.get_db] = db_dep
        self.db = self.Session()
        self.client = TestClient(main.app)
        self.patches = [patch.object(main, 'ensure_fresh_fx_rate', return_value=None),
                        patch.object(main, 'PAYSTACK_SECRET_KEY', PAYSTACK_TEST_SECRET),
                        patch.object(main, 'SessionLocal', self.Session),
                        patch.object(main, 'check_rate_limit', return_value=None),
                        patch.object(main, 'deliver_push_notification', return_value=None)]
        for p in self.patches:
            p.start()
        self.n = 0

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.client.close(); main.app.dependency_overrides.clear()
        self.db.close(); self.engine.dispose()

    # --- fixtures ----------------------------------------------------------
    def paid_business(self, ends_in, plan='starter', status='active', paid=True, **extra):
        """A paid business whose paid-through time is now + ends_in."""
        self.n += 1
        now = datetime.utcnow()
        end = now + ends_in
        biz = main.BusinessProfile(business_code=f'SL-{self.n}', company_name=f'Lifecycle {self.n} Ltd',
                                   currency='NGN (₦)', email=f'billing{self.n}@example.com', subscription_plan=plan)
        self.db.add(biz); self.db.commit()
        sub = main.BusinessSubscription(
            business_id=biz.id, plan=plan, billing_interval='monthly', status=status,
            trial_start_at=end - 60 * DAY, trial_end_at=end - 46 * DAY,
            paid_at=(end - 30 * DAY) if paid else None,
            current_period_start=end - 30 * DAY, current_period_end=end, next_billing_at=end,
            card_verified=True, paystack_customer_code=f'CUS_sim_{self.n}', **extra)
        admin = main.User(username=f'admin{self.n}', email=f'admin{self.n}@example.com', password='x',
                          phone=f'+2348030000{self.n:03d}', role='admin', business_id=biz.id)
        staff = main.User(username=f'staff{self.n}', email=f'staff{self.n}@example.com', password='x',
                          phone=f'+2348040000{self.n:03d}', role='staff', business_id=biz.id)
        self.db.add_all([sub, admin, staff]); self.db.commit()
        self.db.refresh(admin); self.db.refresh(staff)
        return biz, admin, staff

    def auth(self, user, key=None):
        headers = {'Authorization': f'Bearer {main.issue_token(user, self.db)}'}
        if key:
            headers['Idempotency-Key'] = key
        return headers

    def stored(self, biz):
        self.db.expire_all()
        return self.db.query(main.BusinessSubscription).filter_by(business_id=biz.id).one()

    def notices(self, biz, type_prefix='SUBSCRIPTION_'):
        self.db.expire_all()
        return self.db.query(main.Notification).filter(main.Notification.business_id == biz.id,
                                                       main.Notification.type.like(f'{type_prefix}%')).all()

    def webhook(self, event, data):
        raw = json.dumps({'event': event, 'data': data}, separators=(',', ':')).encode()
        sig = hmac.new(PAYSTACK_TEST_SECRET.encode(), raw, hashlib.sha512).hexdigest()
        return self.client.post('/webhooks/paystack', content=raw,
                                headers={'content-type': 'application/json', 'x-paystack-signature': sig})

    def renewal_charge(self, biz, reference, amount_kobo=None):
        amount = amount_kobo if amount_kobo is not None else main.plan_amount_naira('starter', 'monthly') * 100
        return {'reference': reference, 'status': 'success', 'amount': amount, 'currency': 'NGN', 'id': abs(hash(reference)) % 10 ** 9,
                'customer': {'customer_code': self.stored(biz).paystack_customer_code, 'email': biz.email}}

    # --- 1, 2, 16, 17: exact timestamp -------------------------------------
    def test_01_02_active_one_second_before_paused_at_and_after_paid_through(self):
        end = datetime(2026, 10, 1, 23, 30, 0)          # late evening UTC — next day in Lagos
        sub = main.BusinessSubscription(status='active', paid_at=end - 30 * DAY,
                                        current_period_start=end - 30 * DAY, current_period_end=end)
        self.assertEqual(main.subscription_access_state(sub, now=end - timedelta(seconds=1)), ('active', None))
        at, message = main.subscription_access_state(sub, now=end)
        self.assertEqual(at, 'past_due'); self.assertEqual(message, main.SUBSCRIPTION_PAUSED_MESSAGE)
        after, message = main.subscription_access_state(sub, now=end + timedelta(seconds=1))
        self.assertEqual(after, 'past_due'); self.assertIsNotNone(message, 'no access grace after the paid-through time')

    def test_04_operational_workspace_blocked_at_expiry_no_grace(self):
        biz, admin, _ = self.paid_business(-timedelta(seconds=1))
        r = self.client.get('/products/', headers=self.auth(admin))
        self.assertEqual(r.status_code, 402, r.text)
        self.assertIn('paused', r.json()['detail'])
        self.assertIn('data is safe', r.json()['detail'])
        self.assertNotIn('trial', r.json()['detail'].lower(), 'a paid business is not told its trial ended')
        self.assertEqual(self.stored(biz).status, 'past_due')

    def test_still_active_one_minute_before_expiry(self):
        biz, admin, _ = self.paid_business(timedelta(minutes=1))
        self.assertEqual(self.client.get('/products/', headers=self.auth(admin)).status_code, 200)
        self.assertEqual(self.stored(biz).status, 'active')

    def test_11_past_due_window_is_counted_from_the_exact_expiry_not_from_detection(self):
        # Nobody opened the business for 10 days after expiry: it must not get
        # three fresh days now (the old code set now + 3 days).
        biz, admin, _ = self.paid_business(-10 * DAY)
        self.assertEqual(self.client.get('/products/', headers=self.auth(admin)).status_code, 402)
        sub = self.stored(biz)
        self.assertEqual(sub.status, 'expired')
        self.assertEqual(sub.grace_period_ends_at, sub.current_period_end + timedelta(days=main.PAYSTACK_GRACE_PERIOD_DAYS))
        biz2, admin2, _ = self.paid_business(-DAY)
        self.client.get('/products/', headers=self.auth(admin2))
        sub2 = self.stored(biz2)
        self.assertEqual(sub2.status, 'past_due')
        self.assertEqual(sub2.grace_period_ends_at, sub2.current_period_end + 3 * DAY)

    def test_12_window_end_moves_past_due_to_expired_and_says_so_once(self):
        biz, admin, _ = self.paid_business(-DAY)
        self.client.get('/products/', headers=self.auth(admin))
        sub = self.stored(biz)
        sub.current_period_end -= 3 * DAY; self.db.commit()
        for _ in range(3):
            self.assertEqual(self.client.get('/products/', headers=self.auth(admin)).status_code, 402)
        self.assertEqual(self.stored(biz).status, 'expired')
        self.assertEqual(len([n for n in self.notices(biz) if n.type == 'SUBSCRIPTION_EXPIRED']), 1)
        actions = [a.action for a in self.db.query(main.AuditLog).filter_by(business_id=biz.id)]
        self.assertIn('SUBSCRIPTION_PAUSED', actions)
        self.assertIn('SUBSCRIPTION_RENEWAL_WINDOW_ENDED', actions)

    # --- 3, 5, 6, 7, 8, 13, 30: Billing reachable, data kept, roles ----------
    def test_03_30_billing_and_account_essentials_stay_reachable_after_expiry(self):
        biz, admin, _ = self.paid_business(-10 * DAY)
        h = self.auth(admin)
        for path in ('/auth/me', '/subscription/usage', '/subscription/payments', '/plans', '/business-profile/', '/users/me/profile'):
            self.assertNotEqual(self.client.get(path, headers=h).status_code, 402, path)
        for path in ('/products/', '/suppliers/', '/sales/summary'):
            self.assertIn(self.client.get(path, headers=h).status_code, (402, 404), path)
        self.assertEqual(self.client.get('/products/', headers=h).status_code, 402)

    def test_05_expiry_deletes_and_changes_nothing(self):
        biz, admin, staff = self.paid_business(timedelta(minutes=1))
        self.db.add(main.Product(business_id=biz.id, sku='SL-P1', name='Kept Product', category='General',
                                 quantity=7, cost_price=10, retail_price=15))
        self.db.commit()
        sub = self.stored(biz); sub.current_period_end = datetime.utcnow() - 10 * DAY; self.db.commit()
        self.client.get('/products/', headers=self.auth(admin))
        self.db.expire_all()
        product = self.db.query(main.Product).filter_by(business_id=biz.id).one()
        self.assertEqual((product.name, product.quantity), ('Kept Product', 7))
        self.assertEqual({u.username for u in self.db.query(main.User).filter_by(business_id=biz.id)},
                         {admin.username, staff.username})
        self.assertFalse(any(u.disabled for u in self.db.query(main.User).filter_by(business_id=biz.id)))
        self.assertEqual(self.stored(biz).plan, 'starter', 'expiry never downgrades the plan')

    def test_06_staff_get_the_paused_message_and_no_billing_controls(self):
        biz, _, staff = self.paid_business(-HOUR)
        h = self.auth(staff)
        r = self.client.get('/products/', headers=h)
        self.assertEqual(r.status_code, 402)
        self.assertEqual(r.json()['detail'], main.SUBSCRIPTION_PAUSED_MESSAGE)
        me = self.client.get('/auth/me', headers=h).json()
        self.assertEqual(me.get('subscription_blocked_message'), main.SUBSCRIPTION_PAUSED_MESSAGE)
        usage = self.client.get('/subscription/usage', headers=h).json()
        self.assertTrue(usage['access_paused'])
        self.assertFalse(usage['payment_details_visible'])

    def test_07_08_09_13_admin_can_pay_now_after_expiry_staff_cannot_and_retries_are_idempotent(self):
        biz, admin, staff = self.paid_business(-30 * DAY)          # long after the past-due window
        key = uuid.uuid4().hex
        body = {'plan': 'starter', 'billing_interval': 'monthly'}
        with patch.object(main, 'paystack_request', side_effect=fake_initialize) as provider:
            first = self.client.post('/subscription/checkout', json=body, headers=self.auth(admin, key))
            again = self.client.post('/subscription/checkout', json=body, headers=self.auth(admin, key))
            refused = self.client.post('/subscription/checkout', json=body, headers=self.auth(staff, uuid.uuid4().hex))
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(again.status_code, 200, again.text)
        self.assertEqual(first.json()['reference'], again.json()['reference'])
        self.assertEqual(provider.call_count, 1, 'a repeated Pay Now with the same attempt key never initializes twice')
        self.assertEqual(refused.status_code, 403)
        self.assertEqual(self.client.get('/subscription/payments', headers=self.auth(staff)).status_code, 403)
        records = self.db.query(main.PaymentRecord).filter_by(business_id=biz.id).all()
        self.assertEqual(len(records), 1)
        sub = self.stored(biz)
        self.assertEqual(main.effective_subscription_status(sub), 'expired', 'opening checkout never reactivates anything')
        self.assertLess(sub.current_period_end, datetime.utcnow() - 29 * DAY, 'nor extends the paid-through time')
        self.assertEqual(self.client.get('/products/', headers=self.auth(admin)).status_code, 402)

    # --- 9, 14, 15, 16, 17: confirmation is authoritative --------------------
    def _checkout(self, biz, admin):
        with patch.object(main, 'paystack_request', side_effect=fake_initialize):
            r = self.client.post('/subscription/checkout', json={'plan': 'starter', 'billing_interval': 'monthly'},
                                 headers=self.auth(admin, uuid.uuid4().hex))
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()['reference']

    def _verified(self, biz, reference, status='success'):
        record = self.db.query(main.PaymentRecord).filter_by(paystack_reference=reference).one()
        return {'reference': reference, 'status': status, 'amount': record.amount_kobo, 'currency': 'NGN',
                'id': 777000 + record.id, 'customer': {'email': biz.email, 'customer_code': self.stored(biz).paystack_customer_code},
                'metadata': {'business_id': biz.id, 'plan': 'starter', 'billing_interval': 'monthly', 'purpose': 'subscription'}}

    def test_14_pending_confirmation_is_not_failure_and_does_not_unlock(self):
        biz, admin, _ = self.paid_business(-DAY)
        reference = self._checkout(biz, admin)
        with patch.object(main, 'paystack_verify_transaction', return_value=self._verified(biz, reference, 'ongoing')):
            r = self.client.post('/subscription/checkout/confirm', json={'reference': reference}, headers=self.auth(admin))
        self.assertEqual(r.status_code, 202, r.text)
        self.assertEqual(r.json()['status'], 'pending')
        self.assertEqual(self.client.get('/products/', headers=self.auth(admin)).status_code, 402)

    def test_15_17_confirmed_pay_now_restores_access_immediately_and_race_applies_once(self):
        biz, admin, staff = self.paid_business(-2 * DAY)
        reference = self._checkout(biz, admin)
        verified = self._verified(biz, reference)
        with patch.object(main, 'paystack_verify_transaction', return_value=verified):
            confirm = self.client.post('/subscription/checkout/confirm', json={'reference': reference}, headers=self.auth(admin))
            after_confirm = self.stored(biz).current_period_end
            hook = self.webhook('charge.success', verified)
            hook_again = self.webhook('charge.success', verified)
        self.assertEqual(confirm.status_code, 200, confirm.text)
        self.assertEqual(hook.status_code, 200, hook.text)
        self.assertEqual(hook_again.json()['status'], 'already_processed')
        sub = self.stored(biz)
        self.assertEqual(sub.status, 'active')
        self.assertEqual(sub.current_period_end, after_confirm, 'the webhook after the return never extends again')
        self.assertGreater(sub.current_period_end, datetime.utcnow() + 27 * DAY)
        self.assertEqual(self.db.query(main.PaymentRecord).filter_by(business_id=biz.id, status='success').count(), 1)
        self.assertEqual(self.client.get('/products/', headers=self.auth(admin)).status_code, 200)
        self.assertEqual(self.client.get('/auth/me', headers=self.auth(staff)).json().get('subscription_blocked_message'), None)

    def test_15_16_recurring_renewal_webhook_restores_and_duplicate_does_not_extend_twice(self):
        biz, admin, _ = self.paid_business(-HOUR)
        self.assertEqual(self.client.get('/products/', headers=self.auth(admin)).status_code, 402)
        data = self.renewal_charge(biz, 'sim_renewal_1')
        with patch.object(main, 'paystack_verify_transaction', return_value=data):
            self.assertEqual(self.webhook('charge.success', data).status_code, 200)
            end = self.stored(biz).current_period_end
            self.assertEqual(self.webhook('charge.success', data).json()['status'], 'already_processed')
        sub = self.stored(biz)
        self.assertEqual((sub.status, sub.current_period_end, sub.grace_period_ends_at), ('active', end, None))
        self.assertEqual(self.db.query(main.PaymentRecord).filter_by(paystack_reference='sim_renewal_1').count(), 1)
        self.assertEqual(self.client.get('/products/', headers=self.auth(admin)).status_code, 200)

    def test_failed_renewal_reported_before_paid_through_keeps_paid_time(self):
        biz, admin, _ = self.paid_business(2 * HOUR)
        r = self.webhook('invoice.payment_failed', {'customer': {'customer_code': self.stored(biz).paystack_customer_code},
                                                    'subscription': {'subscription_code': 'SUB_sim'}, 'id': 91})
        self.assertEqual(r.status_code, 200, r.text)
        sub = self.stored(biz)
        self.assertEqual((sub.status, sub.payment_status), ('active', 'failed'))
        self.assertEqual(sub.grace_period_ends_at, sub.current_period_end + 3 * DAY)
        self.assertEqual(self.client.get('/products/', headers=self.auth(admin)).status_code, 200)

    def test_failed_renewal_webhook_never_reopens_an_expired_business(self):
        biz, admin, _ = self.paid_business(-10 * DAY, status='expired')
        self.webhook('invoice.payment_failed', {'customer': {'customer_code': self.stored(biz).paystack_customer_code}, 'id': 92})
        self.assertEqual(self.stored(biz).status, 'expired')
        self.assertEqual(self.client.get('/products/', headers=self.auth(admin)).status_code, 402)

    def test_upgrade_after_paid_through_is_refused_and_cancel_stays_reachable(self):
        biz, admin, _ = self.paid_business(-HOUR)
        self.client.get('/products/', headers=self.auth(admin))
        r = self.client.post('/subscription/upgrade-quote', json={'plan': 'business', 'billing_interval': 'monthly'}, headers=self.auth(admin))
        self.assertEqual(r.status_code, 409, r.text)
        self.assertEqual(r.json()['detail'], main.UPGRADE_AFTER_PAID_THROUGH_MESSAGE)
        c = self.client.post('/subscription/cancel', headers=self.auth(admin))
        self.assertEqual(c.status_code, 200, c.text)

    # --- 28, 29: authoritative timestamp over stale status; trial -> paid ------
    def test_28_billing_reports_the_effective_status_not_a_stale_stored_one(self):
        biz, admin, _ = self.paid_business(-HOUR)
        self.assertEqual(self.stored(biz).status, 'active', 'fixture: stale stored status')
        usage = self.client.get('/subscription/usage', headers=self.auth(admin)).json()
        self.assertEqual(usage['status'], 'past_due')
        self.assertEqual(usage['effective_status'], 'past_due')
        self.assertTrue(usage['access_paused'])
        self.assertEqual(usage['blocked_message'], main.SUBSCRIPTION_PAUSED_MESSAGE)
        self.assertTrue(usage['paid_through_at'].endswith('Z'))
        self.assertTrue(usage['renewal_window_ends_at'].endswith('Z'))
        self.assertTrue(usage['server_time'].endswith('Z'))

    def test_29_trial_to_paid_first_payment_activates_and_later_expiry_is_a_paid_pause(self):
        biz, admin, _ = self.paid_business(5 * DAY, status='trialing', paid=False)
        sub = self.stored(biz); sub.trial_end_at = datetime.utcnow() + 5 * DAY; self.db.commit()
        reference = self._checkout(biz, admin)
        with patch.object(main, 'paystack_verify_transaction', return_value=self._verified(biz, reference)):
            self.assertEqual(self.client.post('/subscription/checkout/confirm', json={'reference': reference},
                                              headers=self.auth(admin)).status_code, 200)
        sub = self.stored(biz)
        self.assertEqual(sub.status, 'active'); self.assertIsNotNone(sub.paid_at)
        sub.current_period_end = datetime.utcnow() - HOUR; self.db.commit()
        r = self.client.get('/products/', headers=self.auth(admin))
        self.assertEqual(r.json()['detail'], main.SUBSCRIPTION_PAUSED_MESSAGE)
        # An unpaid trial that ran out keeps the trial wording and is never "active".
        trial_biz, trial_admin, _ = self.paid_business(-HOUR, status='trialing', paid=False)
        tsub = self.stored(trial_biz); tsub.trial_end_at = datetime.utcnow() - HOUR; self.db.commit()
        r = self.client.get('/products/', headers=self.auth(trial_admin))
        self.assertEqual(r.status_code, 402)
        self.assertIn('trial has ended', r.json()['detail'])
        self.assertEqual(self.stored(trial_biz).status, 'expired')

    # --- 19-25, 27: reminders at exact times, once per stage -----------------
    def _stage_after(self, remaining):
        biz, _, _ = self.paid_business(remaining)
        now = datetime.utcnow()
        main.check_subscription_countdown_notifications(self.db, biz, now=now)
        self.db.commit()
        return biz, [n.stage for n in self.notices(biz, 'SUBSCRIPTION_EXPIRY_')]

    def test_19_to_23_each_stage_fires_at_its_exact_offset(self):
        cases = [(7 * DAY - timedelta(minutes=1), '7'), (3 * DAY - timedelta(minutes=1), '3'),
                 (24 * HOUR - timedelta(minutes=1), '24h'), (6 * HOUR - timedelta(minutes=1), '6h'),
                 (HOUR - timedelta(minutes=1), '1h')]
        for remaining, stage in cases:
            _, stages = self._stage_after(remaining)
            self.assertEqual(stages, [stage], f'{remaining} before expiry')
        _, none = self._stage_after(7 * DAY + timedelta(minutes=5))
        self.assertEqual(none, [], 'nothing more than 7 days out')

    def test_25_a_sweep_running_twice_never_sends_a_stage_twice(self):
        biz, _, _ = self.paid_business(5 * HOUR)
        for _ in range(3):
            main.check_subscription_countdown_notifications(self.db, biz); self.db.commit()
        rows = self.notices(biz, 'SUBSCRIPTION_EXPIRY_')
        self.assertEqual([n.stage for n in rows], ['6h'])
        self.assertEqual(rows[0].recipient_user_id, self.db.query(main.User).filter_by(business_id=biz.id, role='admin').one().id,
                         'billing reminders go to the Admin only')

    def test_24_the_pause_is_announced_by_the_background_sweep_without_a_visit(self):
        biz, _, _ = self.paid_business(-timedelta(minutes=2))
        sub = main.get_or_create_subscription(self.db, biz, commit=False)
        main.refresh_subscription_status(self.db, sub)
        main.refresh_subscription_status(self.db, sub)
        paused = [n for n in self.notices(biz) if n.type == 'SUBSCRIPTION_PAYMENT_FAILED']
        self.assertEqual(len(paused), 1)
        self.assertEqual(paused[0].title, 'Subscription paused')
        self.assertIn('data is safe', paused[0].message)

    def test_27_expiry_instant_is_utc_and_serialized_with_a_zone(self):
        end = datetime(2026, 10, 1, 23, 30, 0)
        self.assertEqual(main.to_utc_iso(end), '2026-10-01T23:30:00Z')
        biz, _, _ = self.paid_business(2 * DAY)
        sub = self.stored(biz)
        main.check_subscription_countdown_notifications(self.db, biz, now=sub.current_period_end - 23 * HOUR)
        self.db.commit()
        self.assertEqual([n.stage for n in self.notices(biz, 'SUBSCRIPTION_EXPIRY_')], ['24h'],
                         'stage chosen by the exact timestamp, not the calendar date')


if __name__ == '__main__':
    unittest.main()
