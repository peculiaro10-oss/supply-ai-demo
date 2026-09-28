"""SUB-LIFECYCLE-001 — Cauldra's single renewal engine, duplicate-payment
protection, period anchoring, reminders (in-app + email) with a durable log,
the read-only notification centre while paused, and offline work captured
inside a pause.

Paystack is simulated (Charge Authorization, Initialize, Verify, subscription
fetch/disable) and so is email; nothing leaves the machine. Disposable SQLite.
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

MIN = timedelta(minutes=1)
HOUR = timedelta(hours=1)
DAY = timedelta(days=1)
SECRET = 'sk_test_simulated_engine_only'


class FakePaystack:
    """Just enough of Paystack's API, by reference, to exercise the engine."""

    def __init__(self):
        self.tx, self.charges, self.inits = {}, [], []
        self.charge_outcome = 'success'
        self.subscriptions, self.disabled = {}, []
        self.next_id = 90000

    def _tx(self, reference, status, amount, email):
        self.next_id += 1
        tx = {'reference': reference, 'status': status, 'amount': amount, 'currency': 'NGN', 'id': self.next_id,
              'customer': {'email': email, 'customer_code': 'CUS_sim'},
              'authorization': {'authorization_code': 'AUTH_sim', 'reusable': True, 'channel': 'card',
                                'last4': '4081', 'brand': 'visa', 'exp_month': '12', 'exp_year': '2030'}}
        self.tx[reference] = tx
        return tx

    def request(self, method, path, body=None, timeout=15):
        if path == '/transaction/charge_authorization':
            self.charges.append(body)
            return {'status': True, 'data': dict(self._tx(body['reference'], self.charge_outcome, body['amount'], body['email']))}
        if path == '/transaction/initialize':
            self.inits.append(body)
            self._tx(body['reference'], 'abandoned', body['amount'], body['email'])['metadata'] = body.get('metadata')
            return {'status': True, 'data': {'reference': body['reference'], 'access_code': 'AC_' + body['reference'][-6:],
                                             'authorization_url': 'https://checkout.paystack.com/AC_' + body['reference'][-6:]}}
        raise AssertionError(f'unexpected Paystack call {path}')

    def verify(self, reference):
        return dict(self.tx[reference])

    def pay(self, reference):
        self.tx[reference]['status'] = 'success'

    def fetch(self, code):
        return dict(self.subscriptions[code])

    def disable(self, code, token):
        assert token == self.subscriptions[code]['email_token']
        self.disabled.append(code)
        self.subscriptions[code]['status'] = 'cancelled'


class RenewalEngineTests(unittest.TestCase):
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
        self.ps = FakePaystack()
        self.emails = []
        self.email_failures = 0

        def fake_send(**kw):
            if self.email_failures:
                self.email_failures -= 1
                raise main.EmailDeliveryError('provider_outage')
            self.emails.append(kw)

        self.patches = [
            patch.object(main, 'ensure_fresh_fx_rate', return_value=None),
            patch.object(main, 'PAYSTACK_SECRET_KEY', SECRET),
            patch.object(main, 'SessionLocal', self.Session),
            patch.object(main, 'check_rate_limit', return_value=None),
            patch.object(main, 'deliver_push_notification', return_value=None),
            patch.object(main, 'paystack_request', side_effect=self.ps.request),
            patch.object(main, 'paystack_verify_transaction', side_effect=self.ps.verify),
            patch.object(main, 'paystack_fetch_subscription', side_effect=self.ps.fetch),
            patch.object(main, 'paystack_disable_subscription', side_effect=self.ps.disable),
            patch.object(main, 'paystack_get_or_create_customer', return_value='CUS_sim'),
            patch.object(main, 'send_resend_email', side_effect=fake_send),
            patch.dict(os.environ, {'SUBSCRIPTION_REMINDER_EMAIL_MODE': 'all'}),
        ]
        for p in self.patches:
            p.start()
        self.n = 0

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.client.close(); main.app.dependency_overrides.clear()
        self.db.close(); self.engine.dispose()

    # --- fixtures ----------------------------------------------------------
    def business(self, anchor_offset=-MIN, status='active', paid=True, legacy_code=None, admin_emails=None):
        self.n += 1
        anchor = datetime.utcnow().replace(microsecond=0) + anchor_offset
        biz = main.BusinessProfile(business_code=f'RE-{self.n}', company_name=f'Renew {self.n} Ltd', currency='NGN (₦)',
                                   email=f'owner{self.n}@example.com', subscription_plan='starter', timezone='Africa/Lagos')
        self.db.add(biz); self.db.commit()
        sub = main.BusinessSubscription(
            business_id=biz.id, plan='starter', billing_interval='monthly', status=status,
            trial_start_at=anchor - 60 * DAY, trial_end_at=anchor - 46 * DAY, trial_consent_at=anchor - 60 * DAY,
            paid_at=(anchor - 30 * DAY) if paid else None,
            current_period_start=anchor - 30 * DAY, current_period_end=anchor, next_billing_at=anchor,
            card_verified=True, paystack_authorization_code='AUTH_sim', paystack_authorization_email=f'owner{self.n}@example.com',
            paystack_customer_code='CUS_sim', paystack_subscription_code=legacy_code, card_last4='4081')
        self.db.add(sub)
        emails = admin_emails if admin_emails is not None else [f'admin{self.n}@example.com']
        admins = []
        for i, email in enumerate(emails):
            u = main.User(username=f'admin{self.n}_{i}', email=email, password='x', phone=f'+23480{self.n:03d}{i:05d}',
                          role='admin', business_id=biz.id)
            self.db.add(u); admins.append(u)
        staff = main.User(username=f'staff{self.n}', email=f'staff{self.n}@example.com', password='x',
                          phone=f'+23481{self.n:03d}00000', role='staff', business_id=biz.id)
        self.db.add(staff); self.db.commit()
        for u in admins + [staff]:
            self.db.refresh(u)
        if legacy_code:
            self.ps.subscriptions[legacy_code] = {'status': 'active', 'email_token': 'tok_' + legacy_code, 'most_recent_invoice': {}}
        return biz, anchor, admins, staff

    def auth(self, user, key=None):
        h = {'Authorization': f'Bearer {main.issue_token(user, self.db)}'}
        if key:
            h['Idempotency-Key'] = key
        return h

    def sub(self, biz):
        self.db.expire_all()
        return self.db.query(main.BusinessSubscription).filter_by(business_id=biz.id).one()

    def run_engine(self, biz, now):
        self.db.expire_all()
        return main.run_subscription_renewal(self.db, self.db.get(main.BusinessProfile, biz.id), now)

    def autos(self, biz):
        self.db.expire_all()
        return self.db.query(main.PaymentRecord).filter_by(business_id=biz.id, attempt_source='auto').order_by(main.PaymentRecord.id).all()

    def audits(self, biz, action):
        self.db.expire_all()
        return self.db.query(main.AuditLog).filter_by(business_id=biz.id, action=action).count()

    def pay_now(self, user, key=None):
        return self.client.post('/subscription/checkout', json={'plan': 'starter', 'billing_interval': 'monthly'},
                                headers=self.auth(user, key or uuid.uuid4().hex))

    def webhook(self, event, data):
        raw = json.dumps({'event': event, 'data': data}, separators=(',', ':')).encode()
        sig = hmac.new(SECRET.encode(), raw, hashlib.sha512).hexdigest()
        return self.client.post('/webhooks/paystack', content=raw, headers={'content-type': 'application/json', 'x-paystack-signature': sig})

    # --- RETRY ---------------------------------------------------------------
    def test_on_time_renewal_charges_at_expiry_and_continues_from_the_previous_paid_through(self):
        biz, anchor, _, _ = self.business()
        self.assertEqual(self.run_engine(biz, anchor + 2 * MIN), 'charged')
        sub = self.sub(biz)
        self.assertEqual(sub.status, 'active')
        self.assertEqual(sub.current_period_start, anchor, 'on-time renewal is anchored to the previous paid-through')
        self.assertEqual(sub.current_period_end, main.add_billing_interval(anchor, 'monthly'))
        self.assertEqual(len(self.ps.charges), 1)
        body = self.ps.charges[0]
        self.assertEqual((body['authorization_code'], body['amount'], body['currency']), ('AUTH_sim', main.plan_amount_naira('starter', 'monthly') * 100, 'NGN'))
        self.assertEqual(body['email'], f'owner{self.n}@example.com')
        self.assertIsNone(self.run_engine(biz, anchor + 3 * MIN), 'a renewed period is not charged again')
        self.assertEqual(len(self.ps.charges), 1)

    def test_failed_renewal_retries_every_12_hours_for_3_days_then_stops(self):
        biz, anchor, _, _ = self.business()
        self.ps.charge_outcome = 'failed'
        for hours in range(0, 90, 2):   # a sweep every 2 simulated hours for 90 hours
            self.run_engine(biz, anchor + hours * HOUR + MIN)
        slots = [r.renewal_attempt_slot for r in self.autos(biz)]
        self.assertEqual(slots, [0, 1, 2, 3, 4, 5], 'one attempt per 12-hour slot, six in 3 days')
        self.assertEqual(len(self.ps.charges), 6)
        times = [anchor + main.RENEWAL_RETRY_INTERVAL * s for s in slots]
        self.assertEqual(times[-1], anchor + 60 * HOUR)
        self.assertEqual(self.audits(biz, 'SUBSCRIPTION_RENEWAL_RETRY_SCHEDULED'), 5)
        self.assertEqual(self.audits(biz, 'SUBSCRIPTION_AUTO_RENEWAL_EXHAUSTED'), 1)
        self.assertIsNone(self.run_engine(biz, anchor + 80 * HOUR))
        self.assertEqual(len(self.ps.charges), 6, 'no automatic attempt after the 3-day window')
        self.assertIsNotNone(main.subscription_access_state(self.sub(biz), anchor + 80 * HOUR)[1], 'still paused')

    def test_retry_window_is_counted_from_expiry_not_from_first_detection(self):
        biz, anchor, _, _ = self.business()
        self.ps.charge_outcome = 'failed'
        for hours in range(30, 90, 3):   # nothing ran until 30 hours after expiry
            self.run_engine(biz, anchor + hours * HOUR)
        self.assertEqual([r.renewal_attempt_slot for r in self.autos(biz)], [2, 3, 4, 5])

    def test_late_automatic_success_starts_the_period_at_confirmation(self):
        biz, anchor, _, _ = self.business()
        self.ps.charge_outcome = 'failed'
        self.run_engine(biz, anchor + MIN)
        self.ps.charge_outcome = 'success'
        when = anchor + 24 * HOUR + 5 * MIN
        self.assertEqual(self.run_engine(biz, when), 'charged')
        sub = self.sub(biz)
        self.assertEqual(sub.current_period_start, when, 'a genuinely paused business pays from confirmation')
        self.assertEqual((sub.paused_from, sub.resumed_at), (anchor, when))
        self.assertEqual(self.audits(biz, 'SUBSCRIPTION_RESTORED'), 1)

    def test_manual_pay_now_still_works_after_the_automatic_window(self):
        biz, anchor, admins, _ = self.business(anchor_offset=-4 * DAY)
        self.ps.charge_outcome = 'failed'
        self.assertIsNone(self.run_engine(biz, datetime.utcnow()))
        self.assertEqual(self.ps.charges, [], 'outside the window the engine never charges')
        r = self.pay_now(admins[0])
        self.assertEqual(r.status_code, 200, r.text)
        ref = r.json()['reference']
        self.ps.pay(ref)
        c = self.client.post('/subscription/checkout/confirm', json={'reference': ref}, headers=self.auth(admins[0]))
        self.assertEqual(c.status_code, 200, c.text)
        sub = self.sub(biz)
        self.assertEqual(sub.status, 'active')
        self.assertGreater(sub.current_period_start, anchor)
        self.assertEqual(self.client.get('/products/', headers=self.auth(admins[0])).status_code, 200)

    def test_cancelled_or_cardless_subscriptions_are_never_charged(self):
        biz, anchor, _, _ = self.business()
        sub = self.sub(biz); sub.cancel_at_period_end = True; self.db.commit()
        self.assertIsNone(self.run_engine(biz, anchor + MIN))
        biz2, anchor2, _, _ = self.business()
        sub2 = self.sub(biz2); sub2.paystack_authorization_code = None; self.db.commit()
        self.assertIsNone(self.run_engine(biz2, anchor2 + MIN))
        self.assertEqual(self.ps.charges, [])

    def test_trial_with_consent_converts_at_trial_end(self):
        biz, anchor, _, _ = self.business(status='trialing', paid=False)
        sub = self.sub(biz); sub.trial_end_at = anchor; self.db.commit()
        self.assertEqual(self.run_engine(biz, anchor + MIN), 'charged')
        sub = self.sub(biz)
        self.assertEqual((sub.status, sub.current_period_start), ('active', anchor))
        self.assertIsNotNone(sub.paid_at)

    def test_scheduled_downgrade_is_billed_at_the_boundary(self):
        biz, anchor, _, _ = self.business()
        sub = self.sub(biz)
        sub.pending_downgrade_plan, sub.pending_downgrade_billing_interval, sub.pending_downgrade_effective_at = 'core', 'monthly', anchor
        self.db.commit()
        self.run_engine(biz, anchor + MIN)
        self.assertEqual(self.ps.charges[0]['amount'], main.plan_amount_naira('core', 'monthly') * 100)
        sub = self.sub(biz)
        self.assertEqual((sub.plan, sub.pending_downgrade_plan), ('core', None))

    def test_bank_challenge_is_a_failed_automatic_attempt_not_a_success(self):
        biz, anchor, _, _ = self.business()
        original = self.ps.request

        def challenged(method, path, body=None, timeout=15):
            out = original(method, path, body, timeout)
            out['data'] = {'paused': True, 'reference': body['reference'], 'authorization_url': 'https://checkout.paystack.com/resume/x'}
            self.ps.tx[body['reference']]['status'] = 'pending'
            return out
        with patch.object(main, 'paystack_request', side_effect=challenged):
            self.assertEqual(self.run_engine(biz, anchor + MIN), 'failed')
        self.assertIsNotNone(main.subscription_access_state(self.sub(biz), anchor + MIN)[1])

    def test_lost_charge_response_is_reconciled_by_reference_never_repeated(self):
        biz, anchor, _, _ = self.business()
        original = self.ps.request

        def lost(method, path, body=None, timeout=15):
            original(method, path, body, timeout)          # Paystack charged...
            raise main.PaystackRequestError('timeout', definitive=False)   # ...but the answer was lost
        with patch.object(main, 'paystack_request', side_effect=lost):
            self.assertEqual(self.run_engine(biz, anchor + MIN), 'pending')
        self.assertEqual(self.run_engine(biz, anchor + 20 * MIN), 'charged')
        self.assertEqual(len(self.ps.charges), 1, 'settled by verifying the same reference')
        self.assertEqual(self.sub(biz).status, 'active')

    # --- DUPLICATE PROTECTION ----------------------------------------------
    def test_pay_now_double_click_and_second_device_resume_one_checkout(self):
        biz, anchor, admins, _ = self.business(anchor_offset=-HOUR, admin_emails=['a@example.com', 'b@example.com'])
        first = self.pay_now(admins[0])
        again = self.pay_now(admins[0])            # double click: a new attempt key
        other = self.pay_now(admins[1])            # a second Admin on another device
        for r in (first, again, other):
            self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual({r.json()['reference'] for r in (first, again, other)}, {first.json()['reference']})
        self.assertTrue(again.json().get('resumed'))
        self.assertEqual(len(self.ps.inits), 1, 'one payable checkout')
        self.assertGreaterEqual(self.audits(biz, 'SUBSCRIPTION_DUPLICATE_ATTEMPT_BLOCKED'), 2)

    def test_scheduler_does_not_charge_beside_an_open_pay_now_and_pay_now_waits_for_a_pending_charge(self):
        biz, anchor, admins, _ = self.business(anchor_offset=-HOUR)
        self.pay_now(admins[0])
        self.assertEqual(self.run_engine(biz, datetime.utcnow()), 'waiting')
        self.assertEqual(self.ps.charges, [])
        biz2, anchor2, admins2, _ = self.business()
        self.ps.charge_outcome = 'pending'
        self.assertEqual(self.run_engine(biz2, anchor2 + MIN), 'pending')
        r = self.pay_now(admins2[0])
        self.assertEqual(r.status_code, 409, r.text)
        self.assertEqual(r.json()['detail']['code'], 'RENEWAL_IN_PROGRESS')
        self.assertEqual(len([i for i in self.ps.inits if i['reference'].startswith('cauldra_RE-' + str(self.n))]), 0)

    def test_pending_charge_confirmed_by_webhook_once_then_status_check_is_idempotent(self):
        biz, anchor, admins, _ = self.business()
        self.ps.charge_outcome = 'pending'
        self.run_engine(biz, anchor + MIN)
        ref = self.autos(biz)[0].paystack_reference
        self.ps.pay(ref)
        tx = self.ps.verify(ref)
        self.assertEqual(self.webhook('charge.success', tx).status_code, 200)
        self.assertEqual(self.webhook('charge.success', tx).json()['status'], 'already_processed')
        end = self.sub(biz).current_period_end
        r = self.client.post('/subscription/checkout/confirm', json={'reference': ref}, headers=self.auth(admins[0]))
        self.assertEqual(r.json()['status'], 'success'); self.assertTrue(r.json()['already_processed'])
        sub = self.sub(biz)
        self.assertEqual((sub.status, sub.current_period_end, sub.current_period_start), ('active', end, anchor),
                         'a pending on-time charge confirmed later still continues from the paid-through time')
        self.assertEqual(self.db.query(main.PaymentRecord).filter_by(business_id=biz.id, status='success').count(), 1)

    def test_second_confirmed_payment_for_the_same_period_is_a_duplicate_exception_not_credit(self):
        biz, anchor, admins, _ = self.business(anchor_offset=-HOUR)
        first = self.pay_now(admins[0]).json()['reference']
        # A second checkout that slipped past (older first attempt, other plan...) — simulate directly.
        second = main.PaymentRecord(business_id=biz.id, subscription_id=self.sub(biz).id, plan='starter', billing_interval='monthly',
                                    amount_kobo=main.plan_amount_naira('starter', 'monthly') * 100, currency='NGN',
                                    paystack_reference='cauldra_dup_second', status='initialized', purpose='subscription',
                                    renewal_period_end=anchor, attempt_source='manual',
                                    transaction_metadata=json.dumps({'business_id': biz.id, 'plan': 'starter', 'billing_interval': 'monthly',
                                                                     'purpose': 'subscription', 'customer_email': biz.email.casefold()}))
        self.db.add(second); self.db.commit()
        self.ps._tx('cauldra_dup_second', 'success', second.amount_kobo, biz.email.casefold())
        self.ps.tx['cauldra_dup_second']['metadata'] = {'business_id': biz.id, 'plan': 'starter', 'billing_interval': 'monthly', 'purpose': 'subscription'}
        self.ps.pay(first)
        self.ps.tx[first]['metadata'] = {'business_id': biz.id, 'plan': 'starter', 'billing_interval': 'monthly', 'purpose': 'subscription'}
        self.ps.tx[first]['customer']['email'] = biz.email.casefold()
        ok = self.client.post('/subscription/checkout/confirm', json={'reference': first}, headers=self.auth(admins[0]))
        self.assertEqual(ok.status_code, 200, ok.text)
        end = self.sub(biz).current_period_end
        dup = self.client.post('/subscription/checkout/confirm', json={'reference': 'cauldra_dup_second'}, headers=self.auth(admins[0]))
        self.assertEqual(dup.status_code, 409, dup.text)
        self.assertEqual(dup.json()['status'], 'duplicate_payment_review')
        self.assertEqual(self.sub(biz).current_period_end, end, 'never extended twice')
        self.assertEqual(self.db.query(main.PaymentRecord).filter_by(paystack_reference='cauldra_dup_second').one().status, 'duplicate_payment_review')
        self.assertEqual(self.audits(biz, 'SUBSCRIPTION_DUPLICATE_PAYMENT'), 1)

    def test_legacy_paystack_subscription_is_disabled_before_cauldra_charges(self):
        biz, anchor, _, _ = self.business(legacy_code='SUB_legacy1')
        self.assertEqual(self.run_engine(biz, anchor + 13 * HOUR), 'charged')
        self.assertEqual(self.ps.disabled, ['SUB_legacy1'])
        self.assertIsNone(self.sub(biz).paystack_subscription_code)
        self.assertEqual(len(self.ps.charges), 1)

    def test_legacy_subscription_that_already_charged_this_cycle_is_left_to_its_webhook(self):
        biz, anchor, _, _ = self.business(legacy_code='SUB_legacy2')
        self.ps.subscriptions['SUB_legacy2']['most_recent_invoice'] = {'status': 'success', 'paid_at': (anchor + 5 * MIN).isoformat() + 'Z'}
        self.assertEqual(self.run_engine(biz, anchor + 13 * HOUR), 'provider_charged')
        self.assertEqual((self.ps.charges, self.ps.disabled), ([], []))

    def test_legacy_subscription_is_retired_well_before_its_next_charge(self):
        biz, anchor, _, _ = self.business(anchor_offset=10 * DAY, legacy_code='SUB_legacy3')
        self.assertEqual(self.run_engine(biz, datetime.utcnow()), 'retired')
        self.assertEqual(self.ps.disabled, ['SUB_legacy3'])
        # One already cancelled at Paystack is only unlinked, and the audit says so.
        biz2, _, _, _ = self.business(anchor_offset=10 * DAY, legacy_code='SUB_legacy6')
        self.ps.subscriptions['SUB_legacy6']['status'] = 'cancelled'
        self.assertEqual(self.run_engine(biz2, datetime.utcnow()), 'retired')
        self.assertEqual(self.ps.disabled, ['SUB_legacy3'])
        self.assertIsNone(self.sub(biz2).paystack_subscription_code)
        log = self.db.query(main.AuditLog).filter_by(business_id=biz2.id, action='SUBSCRIPTION_PROVIDER_SCHEDULE_RETIRED').one()
        self.assertIn('already inactive at Paystack', log.description)

    def test_pay_now_after_a_failed_renewal_disables_the_old_paystack_subscription(self):
        biz, anchor, admins, _ = self.business(anchor_offset=-2 * HOUR, legacy_code='SUB_legacy4')
        ref = self.pay_now(admins[0]).json()['reference']
        self.ps.pay(ref)
        self.ps.tx[ref]['metadata'] = {'business_id': biz.id, 'plan': 'starter', 'billing_interval': 'monthly', 'purpose': 'subscription'}
        self.ps.tx[ref]['customer']['email'] = biz.email.casefold()
        r = self.client.post('/subscription/checkout/confirm', json={'reference': ref}, headers=self.auth(admins[0]))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()['recurring_setup'], 'complete')
        self.assertEqual(self.ps.disabled, ['SUB_legacy4'], 'the old subscription can never charge this business again')
        self.assertIsNone(self.sub(biz).paystack_subscription_code)

    def test_legacy_renewal_webhook_for_an_already_renewed_period_is_a_duplicate_exception(self):
        biz, anchor, _, _ = self.business()
        self.run_engine(biz, anchor + MIN)                     # Cauldra renewed this period
        end = self.sub(biz).current_period_end
        legacy = {'reference': 'legacy_paystack_charge', 'status': 'success', 'amount': main.plan_amount_naira('starter', 'monthly') * 100,
                  'currency': 'NGN', 'id': 555, 'customer': {'customer_code': 'CUS_sim', 'email': biz.email}, 'paid_at': (anchor + 2 * MIN).isoformat() + 'Z'}
        self.ps.tx['legacy_paystack_charge'] = legacy
        self.assertEqual(self.webhook('charge.success', legacy).status_code, 200)
        self.assertEqual(self.sub(biz).current_period_end, end)
        self.assertEqual(self.db.query(main.PaymentRecord).filter_by(paystack_reference='legacy_paystack_charge').one().status, 'duplicate_payment_review')

    def test_legacy_on_time_renewal_webhook_arriving_late_keeps_the_anchor(self):
        biz, anchor, _, _ = self.business(anchor_offset=-3 * HOUR)
        legacy = {'reference': 'legacy_on_time', 'status': 'success', 'amount': main.plan_amount_naira('starter', 'monthly') * 100,
                  'currency': 'NGN', 'id': 556, 'customer': {'customer_code': 'CUS_sim', 'email': biz.email}, 'paid_at': (anchor + MIN).isoformat() + 'Z'}
        self.ps.tx['legacy_on_time'] = legacy
        self.assertEqual(self.webhook('charge.success', legacy).status_code, 200)
        self.assertEqual(self.sub(biz).current_period_start, anchor)

    def test_legacy_charge_on_paystacks_own_earlier_calendar_day_renews_the_coming_period(self):
        # QA TEST evidence: Paystack charges a subscription that started on the
        # 31st on the 28th. That early charge is a renewal, not a duplicate.
        biz, anchor, _, _ = self.business(anchor_offset=2 * DAY + 5 * MIN)
        early = {'reference': 'legacy_early', 'status': 'success', 'amount': main.plan_amount_naira('starter', 'monthly') * 100,
                 'currency': 'NGN', 'id': 557, 'customer': {'customer_code': 'CUS_sim', 'email': biz.email}, 'paid_at': datetime.utcnow().isoformat() + 'Z'}
        self.ps.tx['legacy_early'] = early
        self.assertEqual(self.webhook('charge.success', early).status_code, 200)
        sub = self.sub(biz)
        self.assertEqual(sub.current_period_start, anchor, 'continues from the paid-through time; no time lost')
        self.assertEqual(self.db.query(main.PaymentRecord).filter_by(paystack_reference='legacy_early').one().status, 'success')

    def test_legacy_subscription_is_not_disabled_while_its_own_charge_is_imminent(self):
        biz, anchor, _, _ = self.business(anchor_offset=2 * DAY, legacy_code='SUB_legacy5')
        self.ps.subscriptions['SUB_legacy5']['next_payment_date'] = (datetime.utcnow() + 20 * MIN).isoformat() + 'Z'
        self.assertEqual(self.run_engine(biz, datetime.utcnow()), 'provider_charging')
        self.assertEqual(self.ps.disabled, [])
        self.ps.subscriptions['SUB_legacy5']['next_payment_date'] = (datetime.utcnow() + 5 * DAY).isoformat() + 'Z'
        self.assertEqual(self.run_engine(biz, datetime.utcnow()), 'retired')

    # --- REMINDERS -----------------------------------------------------------
    def lifecycle(self, biz, now):
        self.db.expire_all()
        main.record_subscription_reminders(self.db, self.db.get(main.BusinessProfile, biz.id), self.sub(biz), now)
        main.send_pending_subscription_reminder_emails(self.db, self.db.get(main.BusinessProfile, biz.id), now)
        self.db.commit()

    def reminders(self, biz, channel):
        self.db.expire_all()
        return self.db.query(main.SubscriptionReminderDelivery).filter_by(business_id=biz.id, channel=channel).all()

    def test_every_stage_in_app_once_and_only_the_policy_stages_by_email(self):
        # Owner policy (pre-freeze): 24 h and 5 h before by email; 7d/3d/1h in-app
        # only; a business with a saved card is not emailed at the pause (its
        # first failed charge is), and gets the end-of-window email.
        biz, anchor, _, _ = self.business(anchor_offset=8 * DAY)
        sub = self.sub(biz); anchor = sub.current_period_end
        offsets = [(-7 * DAY, '7d'), (-3 * DAY, '3d'), (-24 * HOUR, '24h'), (-5 * HOUR, '5h'), (-HOUR, '1h')]
        for before, stage in offsets:
            for _ in range(2):                    # the sweep runs twice
                self.lifecycle(biz, anchor + before + MIN)
        sub.status = 'past_due'; self.db.commit()
        for after, stage in [(MIN, 'paused'), (24 * HOUR + MIN, 'paused_24h'), (72 * HOUR + MIN, 'window_end')]:
            for _ in range(2):
                self.lifecycle(biz, anchor + after)
        self.lifecycle(biz, anchor + 48 * HOUR)    # no stage between +24h and +72h
        stages = ['7d', '3d', '24h', '5h', '1h', 'paused', 'paused_24h', 'window_end']
        emailed = ['24h', '5h', 'window_end']
        self.assertEqual([r.stage for r in self.reminders(biz, 'in_app')], stages)
        self.assertEqual([r.stage for r in self.reminders(biz, 'email')], emailed)
        self.assertEqual(len(self.emails), len(emailed), 'one email per emailed stage and address')
        self.assertEqual(len({e['idempotency_key'] for e in self.emails}), len(emailed))
        types = [n.type for n in self.db.query(main.Notification).filter_by(business_id=biz.id).filter(main.Notification.type.like('SUBSCRIPTION_REMINDER_%'))]
        self.assertEqual(len(types), len(stages))

    def test_long_lapsed_business_is_not_emailed_now(self):
        biz, anchor, _, _ = self.business(anchor_offset=-20 * DAY, status='expired')
        self.lifecycle(biz, datetime.utcnow())
        self.assertEqual((self.reminders(biz, 'email'), self.emails), ([], []))

    def test_recipients_are_admins_deduplicated_never_staff_business_email_fallback(self):
        biz, anchor, admins, staff = self.business(anchor_offset=4 * HOUR, admin_emails=['Boss@Example.com', 'boss@example.com', 'second@example.com', 'not-an-email'])
        self.lifecycle(biz, datetime.utcnow())
        self.assertEqual(sorted(e['to_email'] for e in self.emails), ['boss@example.com', 'second@example.com'])
        self.assertNotIn(staff.email, [e['to_email'] for e in self.emails])
        biz2, _, _, _ = self.business(anchor_offset=4 * HOUR, admin_emails=[''])
        self.emails.clear()
        self.lifecycle(biz2, datetime.utcnow())
        self.assertEqual([e['to_email'] for e in self.emails], [biz2.email.casefold()])

    def test_email_failure_keeps_in_app_and_retries_with_the_same_key_once(self):
        biz, anchor, _, _ = self.business(anchor_offset=4 * HOUR)
        self.email_failures = 1
        self.lifecycle(biz, datetime.utcnow())
        self.assertEqual(len(self.reminders(biz, 'in_app')), 1, 'in-app reminder stands on its own')
        row = self.reminders(biz, 'email')[0]
        self.assertEqual((row.status, row.last_error_category), ('failed', 'provider_outage'))
        self.lifecycle(biz, datetime.utcnow() + 5 * MIN)
        self.lifecycle(biz, datetime.utcnow() + 10 * MIN)
        row = self.reminders(biz, 'email')[0]
        self.assertEqual((row.status, row.attempts), ('sent', 2))
        self.assertEqual(len(self.emails), 1)

    def test_email_mode_off_suppresses_email_but_not_in_app(self):
        biz, anchor, _, _ = self.business(anchor_offset=4 * HOUR)
        with patch.dict(os.environ, {'SUBSCRIPTION_REMINDER_EMAIL_MODE': 'off'}):
            self.lifecycle(biz, datetime.utcnow())
        self.assertEqual(self.emails, [])
        self.assertEqual([r.status for r in self.reminders(biz, 'email')], ['suppressed'])
        self.assertEqual(len(self.reminders(biz, 'in_app')), 1)

    def test_email_allowlist_mode_only_emails_allowed_addresses(self):
        biz, anchor, _, _ = self.business(anchor_offset=4 * HOUR, admin_emails=['qa@owner-test.example', 'customer@elsewhere.example'])
        with patch.dict(os.environ, {'SUBSCRIPTION_REMINDER_EMAIL_MODE': 'allowlist', 'SUBSCRIPTION_REMINDER_EMAIL_ALLOWLIST': '@owner-test.example'}):
            self.lifecycle(biz, datetime.utcnow())
        self.assertEqual([e['to_email'] for e in self.emails], ['qa@owner-test.example'])

    def test_reminder_text_uses_business_timezone_and_says_data_is_safe(self):
        biz, anchor, _, _ = self.business(anchor_offset=-MIN)
        sub = self.sub(biz); sub.current_period_end = datetime(2026, 10, 1, 23, 30); self.db.commit()
        title, message = main.subscription_reminder_text(sub, self.db.get(main.BusinessProfile, biz.id), 'paused', sub.current_period_end)
        self.assertIn('02 Oct 2026, 00:30 (Africa/Lagos)', message)
        self.assertIn('data is safe', message)
        self.assertIn('retry your saved card automatically', message)

    # --- NOTIFICATION CENTRE / STAFF -----------------------------------------
    def test_paused_users_read_the_notification_centre_but_not_the_workspace(self):
        biz, anchor, admins, staff = self.business(anchor_offset=-HOUR)
        self.lifecycle(biz, datetime.utcnow())
        for user in (admins[0], staff):
            h = self.auth(user)
            r = self.client.get('/notifications', headers=h)
            self.assertEqual(r.status_code, 200, r.text)
            self.assertTrue(r.json()['read_only'])
            self.assertEqual(self.client.get('/notifications/unread-count', headers=h).status_code, 200)
            self.assertEqual(self.client.post('/notifications/mark-all-read', headers=h).status_code, 200)
            self.assertEqual(self.client.get('/products/', headers=h).status_code, 402)
        notes = self.client.get('/notifications', headers=self.auth(admins[0])).json()['notifications']
        self.assertTrue(any(n['type'] == 'SUBSCRIPTION_REMINDER_PAUSED' for n in notes))
        self.assertEqual(self.client.post('/subscription/checkout', json={'plan': 'starter', 'billing_interval': 'monthly'},
                                          headers=self.auth(staff, uuid.uuid4().hex)).status_code, 403)

    def test_billing_reports_renewal_in_progress_and_the_retry_schedule(self):
        biz, anchor, admins, _ = self.business(anchor_offset=-HOUR)
        self.ps.charge_outcome = 'pending'
        self.run_engine(biz, datetime.utcnow())
        usage = self.client.get('/subscription/usage', headers=self.auth(admins[0])).json()
        self.assertTrue(usage['access_paused'])
        self.assertTrue(usage['renewal_in_progress'])
        self.assertEqual(usage['auto_renewal']['ends_at'], main.to_utc_iso(anchor + 3 * DAY))

    # --- OFFLINE -------------------------------------------------------------
    def test_offline_work_captured_inside_a_past_pause_is_refused_not_applied(self):
        biz, anchor, _, _ = self.business()
        self.ps.charge_outcome = 'failed'
        self.run_engine(biz, anchor + MIN)
        self.ps.charge_outcome = 'success'
        self.run_engine(biz, anchor + 12 * HOUR + MIN)
        sub = self.sub(biz)
        self.assertIsNotNone(main.offline_capture_paused_reason(self.db, biz.id, anchor + 2 * HOUR))
        self.assertIsNone(main.offline_capture_paused_reason(self.db, biz.id, anchor - MIN), 'captured before expiry is kept and applied')
        self.assertIsNone(main.offline_capture_paused_reason(self.db, biz.id, sub.resumed_at + MIN))
        source = open(os.path.join(os.path.dirname(main.__file__), 'offline_access.py'), encoding='utf-8').read()
        self.assertIn('failure("SUBSCRIPTION_PAUSED", paused_reason, 409)', source)


if __name__ == '__main__':
    unittest.main()
