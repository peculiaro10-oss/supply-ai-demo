"""PRICE-UPGRADE-001 — an upgrade payment buys what it charges for.

Found in the read-only production preflight (2026-09-29) against the frozen RC
7763a26: the quote charged the new plan's full term price minus the unused
credit, but the paid-through time stayed where it was, so the customer paid
(almost) a full new term for the rest of the old period and paid again at the
old renewal date. /subscription/change-plan also let a paying Admin move to a
higher tier without paying whenever the new list price was not higher.

Policy now (one model): the upgrade payment buys one full term of the new plan
and interval, starting when Paystack confirms it; the unused value of the old
paid period is credited; the next renewal is anchored at the new paid-through.
A credit worth more than one new term (annual -> monthly early in the year) is
refused. A higher tier always needs the paid upgrade flow.

Disposable SQLite; real endpoints; Paystack is a fake boundary (no network).
"""
import os
os.environ['PYTHON_DOTENV_DISABLED'] = '1'
os.environ['SUPPLY_AI_SKIP_DB_STARTUP_CHECK'] = 'true'
os.environ.setdefault('DATABASE_URL', 'postgresql+psycopg://test:test@127.0.0.1:65432/cauldra_test')
os.environ.setdefault('SUPPLY_AI_SECRET_KEY', 'isolated-test-secret-012345678901234567890123456789')
import copy
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
SECRET = 'sk_test_simulated_price_upgrade_only'
PLANS = ['core', 'starter', 'business', 'enterprise']   # Starter, Business, Premium, Enterprise
KOBO = lambda plan, interval: main.plan_amount_naira(plan, interval) * 100


class FakePaystack:
    """Initialize records the exact body Cauldra sends; verify answers from a
    table the test controls. No network, no real keys."""
    def __init__(self):
        self.initialized = {}
        self.verify = {}

    def request(self, method, path, body=None, timeout=15):
        if path == '/transaction/initialize':
            self.initialized[body['reference']] = copy.deepcopy(body)
            return {'data': {'access_code': 'AC_' + body['reference'][-6:],
                             'authorization_url': 'https://checkout.paystack.com/AC_SIM', 'reference': body['reference']}}
        if path.startswith('/transaction/verify/'):
            ref = path.rsplit('/', 1)[1]
            return {'data': self.verify.get(ref, {'reference': ref, 'status': 'abandoned'})}
        raise AssertionError(f'unexpected Paystack call {method} {path}')

    def pay(self, reference, paid_at=None, amount=None, id_=None):
        body = self.initialized[reference]
        tx = {'reference': reference, 'status': 'success', 'amount': body['amount'] if amount is None else amount,
              'currency': body['currency'], 'id': id_ or int(uuid.uuid4().hex[:9], 16),
              'metadata': body['metadata'], 'customer': {'email': body['email'], 'customer_code': 'CUS_sim'},
              'paid_at': (paid_at or datetime.utcnow()).replace(microsecond=0).isoformat() + 'Z',
              'authorization': {'reusable': True, 'channel': 'card', 'authorization_code': 'AUTH_sim', 'last4': '4081'}}
        self.verify[reference] = tx
        return tx


def quote_math(from_plan, from_interval, to_plan, to_interval, fraction_elapsed=None, minutes_left=None):
    start = datetime(2026, 10, 1)
    end = main.add_billing_interval(start, from_interval)
    now = end - timedelta(minutes=minutes_left) if minutes_left is not None else start + (end - start) * fraction_elapsed
    return main.upgrade_quote_amounts(from_plan, from_interval, to_plan, to_interval, start, end, now)


class QuoteMathTests(unittest.TestCase):
    """The pure formula, for every interval pairing and period position."""

    def test_credit_is_the_unused_value_and_due_is_one_new_term_minus_it(self):
        for fp in PLANS[:-1]:
            for fi in ('monthly', 'annual'):
                for tp in PLANS[PLANS.index(fp) + 1:]:
                    for ti in ('monthly', 'annual'):
                        for elapsed in (0.0, 0.25, 0.5, 0.75):
                            credit = round(KOBO(fp, fi) * (1 - elapsed))
                            with self.subTest(f=(fp, fi), t=(tp, ti), elapsed=elapsed):
                                if credit > KOBO(tp, ti) - main.UPGRADE_MINIMUM_CHARGE_KOBO:
                                    with self.assertRaises(main.UpgradeNotQuotable):
                                        quote_math(fp, fi, tp, ti, elapsed)
                                    continue
                                q = quote_math(fp, fi, tp, ti, elapsed)
                                self.assertEqual(q['unused_credit_kobo'], credit)
                                self.assertEqual(q['new_price_kobo'], KOBO(tp, ti))
                                self.assertEqual(q['amount_due_kobo'], KOBO(tp, ti) - credit)
                                self.assertGreaterEqual(q['amount_due_kobo'], main.UPGRADE_MINIMUM_CHARGE_KOBO)
                                self.assertIsInstance(q['amount_due_kobo'], int)

    def test_monthly_to_monthly_at_half(self):
        q = quote_math('core', 'monthly', 'starter', 'monthly', 0.5)       # Starter M -> Business M
        self.assertEqual((q['unused_credit_kobo'], q['amount_due_kobo']), (250_000, 1_750_000))

    def test_monthly_to_annual_near_expiry_buys_a_full_year(self):
        q = quote_math('starter', 'monthly', 'enterprise', 'annual', minutes_left=1)   # Business M -> Enterprise A
        self.assertLess(q['unused_credit_kobo'], 100)
        self.assertEqual(q['new_price_kobo'], 210_000_000)
        now = datetime(2026, 10, 31, 23, 59)
        self.assertEqual(q['new_paid_through_if_paid_now'], main.add_billing_interval(now, 'annual'))

    def test_annual_to_annual_at_half(self):
        q = quote_math('core', 'annual', 'business', 'annual', 0.5)        # Starter A -> Premium A
        self.assertEqual((q['unused_credit_kobo'], q['amount_due_kobo']), (2_500_000, 47_500_000))

    def test_monthly_to_annual_at_three_quarters(self):
        q = quote_math('business', 'monthly', 'enterprise', 'annual', 0.75)  # Premium M -> Enterprise A
        self.assertEqual((q['unused_credit_kobo'], q['amount_due_kobo']), (1_250_000, 208_750_000))

    def test_annual_to_monthly_early_is_refused_not_sold_for_the_floor(self):
        with self.assertRaises(main.UpgradeNotQuotable) as ctx:
            quote_math('business', 'annual', 'enterprise', 'monthly', 0.0)  # Premium A -> Enterprise M
        self.assertEqual(ctx.exception.unused_credit_kobo, 50_000_000)
        for elapsed in (0.25, 0.5):
            with self.assertRaises(main.UpgradeNotQuotable):
                quote_math('business', 'annual', 'enterprise', 'monthly', elapsed)
        q = quote_math('business', 'annual', 'enterprise', 'monthly', 0.75)
        self.assertEqual((q['unused_credit_kobo'], q['amount_due_kobo']), (12_500_000, 7_500_000))

    def test_full_period_start_credits_the_whole_price(self):
        q = quote_math('starter', 'monthly', 'enterprise', 'monthly', 0.0)
        self.assertEqual((q['unused_credit_kobo'], q['amount_due_kobo']), (2_000_000, 18_000_000))

    def test_minimum_charge_edge(self):
        start, end = datetime(2026, 10, 1), datetime(2026, 11, 1)
        # Credit exactly one term minus the floor: allowed, due == floor.
        with patch.dict(main.PLAN_CONFIG, {'business': {**main.PLAN_CONFIG['business'], 'annual_price': 200_000 - 1}}):
            q = main.upgrade_quote_amounts('business', 'annual', 'enterprise', 'monthly', start, main.add_billing_interval(start, 'annual'), start)
            self.assertEqual(q['amount_due_kobo'], main.UPGRADE_MINIMUM_CHARGE_KOBO)
        with patch.dict(main.PLAN_CONFIG, {'business': {**main.PLAN_CONFIG['business'], 'annual_price': 200_000}}):
            with self.assertRaises(main.UpgradeNotQuotable):
                main.upgrade_quote_amounts('business', 'annual', 'enterprise', 'monthly', start, main.add_billing_interval(start, 'annual'), start)
        with self.assertRaises(ValueError):
            main.upgrade_quote_amounts('core', 'monthly', 'starter', 'monthly', end, start, start)

    def test_remaining_fraction_is_clamped(self):
        start, end = datetime(2026, 10, 1), datetime(2026, 11, 1)
        before = main.upgrade_quote_amounts('core', 'monthly', 'starter', 'monthly', start, end, start - DAY)
        after = main.upgrade_quote_amounts('core', 'monthly', 'starter', 'monthly', start, end, end + DAY)
        self.assertEqual(before['unused_fraction'], 1.0)
        self.assertEqual(after['unused_fraction'], 0.0)


class UpgradeFlowTests(unittest.TestCase):
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
        self.paystack = FakePaystack()
        self.patches = [patch.object(main, 'ensure_fresh_fx_rate', return_value=None),
                        patch.object(main, 'PAYSTACK_SECRET_KEY', SECRET),
                        patch.object(main, 'SessionLocal', self.Session),
                        patch.object(main, 'check_rate_limit', return_value=None),
                        patch.object(main, 'deliver_push_notification', return_value=None),
                        patch.object(main, 'paystack_request', side_effect=self.paystack.request)]
        for p in self.patches:
            p.start()
        self.n = 0

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.client.close(); main.app.dependency_overrides.clear()
        self.db.close(); self.engine.dispose()

    def business(self, plan='starter', interval='monthly', elapsed=0.5, status='active', minutes_left=None):
        self.n += 1
        now = datetime.utcnow()
        length = main.add_billing_interval(datetime(2026, 10, 1), interval) - datetime(2026, 10, 1)
        if minutes_left is not None:
            end = now + timedelta(minutes=minutes_left)
            start = end - length
        else:
            start = now - length * elapsed
            end = start + length
        biz = main.BusinessProfile(business_code=f'PU-{self.n}', company_name=f'Upgrade {self.n} Ltd', currency='NGN (₦)',
                                   email=f'billing{self.n}@example.com', subscription_plan=plan, billing_interval=interval)
        self.db.add(biz); self.db.commit()
        sub = main.BusinessSubscription(business_id=biz.id, plan=plan, billing_interval=interval, status=status,
                                        trial_start_at=start - 20 * DAY,
                                        trial_end_at=(now + 7 * DAY) if status == 'trialing' else start - 6 * DAY,
                                        paid_at=None if status == 'trialing' else start,
                                        current_period_start=start, current_period_end=end, next_billing_at=end,
                                        card_verified=True, paystack_customer_code='CUS_sim',
                                        paystack_authorization_code='AUTH_sim', trial_consent_at=start - 20 * DAY)
        admin = main.User(username=f'padmin{self.n}', email=f'padmin{self.n}@example.com', password='x',
                          phone=f'+2348050000{self.n:03d}', role='admin', business_id=biz.id)
        self.db.add_all([sub, admin]); self.db.commit(); self.db.refresh(admin)
        return biz, admin

    def auth(self, user, key=True):
        headers = {'Authorization': f'Bearer {main.issue_token(user, self.db)}'}
        if key:
            headers['Idempotency-Key'] = uuid.uuid4().hex
        return headers

    def stored(self, biz):
        self.db.expire_all()
        return self.db.query(main.BusinessSubscription).filter_by(business_id=biz.id).one()

    def quote(self, admin, plan, interval):
        return self.client.post('/subscription/upgrade-quote', json={'plan': plan, 'billing_interval': interval}, headers=self.auth(admin, key=False))

    def checkout(self, admin, quote_reference):
        return self.client.post('/subscription/upgrade-checkout', json={'quote_reference': quote_reference}, headers=self.auth(admin))

    def webhook(self, reference):
        raw = json.dumps({'event': 'charge.success', 'data': {'reference': reference}}, separators=(',', ':')).encode()
        sig = hmac.new(SECRET.encode(), raw, hashlib.sha512).hexdigest()
        return self.client.post('/webhooks/paystack', content=raw, headers={'content-type': 'application/json', 'x-paystack-signature': sig})

    def upgrade(self, admin, plan, interval):
        q = self.quote(admin, plan, interval)
        self.assertEqual(q.status_code, 200, q.text)
        c = self.checkout(admin, q.json()['quote_reference'])
        self.assertEqual(c.status_code, 200, c.text)
        return q.json(), c.json()

    # --- regression 1: Business Monthly -> Enterprise Annual near expiry ------
    def test_near_expiry_monthly_to_annual_buys_a_full_new_year(self):
        biz, admin = self.business('starter', 'monthly', minutes_left=60)
        old_end = self.stored(biz).current_period_end
        quote, checkout = self.upgrade(admin, 'enterprise', 'annual')
        self.assertLess(quote['unused_credit'], 30)                          # ~1 h of ₦20,000/month
        self.assertEqual(quote['amount_due_kobo'], 210_000_000 - round(2_000_000 * quote['unused_fraction']))
        self.assertEqual(checkout['amount_kobo'], quote['amount_due_kobo'])
        sent = self.paystack.initialized[checkout['reference']]
        self.assertEqual(sent['amount'], quote['amount_due_kobo'], 'Paystack gets the quote amount, in kobo, once')
        self.assertEqual(sent['currency'], 'NGN')
        self.assertNotIn('plan', sent, 'no Paystack plan/subscription object is used')
        paid_at = datetime.utcnow().replace(microsecond=0)
        self.paystack.pay(checkout['reference'], paid_at=paid_at)
        self.assertEqual(self.webhook(checkout['reference']).status_code, 200)
        sub = self.stored(biz)
        self.assertEqual((sub.plan, sub.billing_interval, sub.status), ('enterprise', 'annual', 'active'))
        self.assertEqual(sub.current_period_start, paid_at)
        self.assertEqual(sub.current_period_end, main.add_billing_interval(paid_at, 'annual'))
        self.assertEqual(sub.next_billing_at, sub.current_period_end)
        self.assertGreater(sub.current_period_end, old_end + 360 * DAY, 'the ₦2.1m buys a year, not an hour')
        self.assertEqual(main.renewal_anchor(sub), sub.current_period_end)

    # --- regression 2: Premium Annual -> Enterprise Monthly near the start ----
    def test_annual_to_monthly_near_start_is_refused_and_later_priced_fairly(self):
        biz, admin = self.business('business', 'annual', elapsed=0.02)
        r = self.quote(admin, 'enterprise', 'monthly')
        self.assertEqual(r.status_code, 409, r.text)
        self.assertEqual(r.json()['detail']['code'], 'UPGRADE_CREDIT_EXCEEDS_TERM')
        self.assertIn('Enterprise Annual', r.json()['detail']['message'])
        self.assertEqual(self.db.query(main.SubscriptionUpgradeQuote).count(), 0)
        ok = self.quote(admin, 'enterprise', 'annual')
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertGreater(ok.json()['amount_due_kobo'], 150_000_000)
        biz2, admin2 = self.business('business', 'annual', elapsed=0.75)
        quote, checkout = self.upgrade(admin2, 'enterprise', 'monthly')
        self.assertAlmostEqual(quote['amount_due'], 200_000 - 125_000, delta=5)
        paid_at = datetime.utcnow().replace(microsecond=0)
        self.paystack.pay(checkout['reference'], paid_at=paid_at)
        self.webhook(checkout['reference'])
        sub = self.stored(biz2)
        self.assertEqual(sub.current_period_end, main.add_billing_interval(paid_at, 'monthly'), 'one month of Enterprise, not months')

    # --- regression 3: change-plan cannot grant a higher tier -----------------
    def test_change_plan_refuses_every_higher_tier_for_a_paying_business(self):
        cases = [('starter', 'annual', 'enterprise', 'monthly'),     # Business A ₦200k -> Enterprise M ₦200k
                 ('core', 'annual', 'starter', 'monthly'),           # Starter A ₦50k -> Business M ₦20k
                 ('business', 'annual', 'enterprise', 'monthly'),    # Premium A ₦500k -> Enterprise M ₦200k
                 ('core', 'monthly', 'starter', 'monthly')]
        for fp, fi, tp, ti in cases:
            with self.subTest(f=(fp, fi), t=(tp, ti)):
                biz, admin = self.business(fp, fi, elapsed=0.5)
                r = self.client.post('/subscription/change-plan', json={'plan': tp, 'billing_interval': ti}, headers=self.auth(admin, key=False))
                self.assertEqual(r.status_code, 402, r.text)
                sub = self.stored(biz)
                self.assertEqual((sub.plan, sub.billing_interval), (fp, fi))

    def test_same_plan_interval_changes_follow_the_owner_policy(self):
        biz, admin = self.business('starter', 'monthly', elapsed=0.5)
        r = self.client.post('/subscription/change-plan', json={'plan': 'starter', 'billing_interval': 'annual'}, headers=self.auth(admin, key=False))
        self.assertEqual(r.status_code, 402, 'monthly -> annual while active stays blocked')
        biz, admin = self.business('starter', 'annual', elapsed=0.5)
        r = self.client.post('/subscription/change-plan', json={'plan': 'starter', 'billing_interval': 'monthly'}, headers=self.auth(admin, key=False))
        self.assertEqual(r.status_code, 409, r.text)
        self.assertEqual(r.json()['detail']['code'], 'INTERVAL_CHANGE_AT_RENEWAL')
        self.assertEqual(self.stored(biz).billing_interval, 'annual', 'annual -> monthly is not an API bypass')
        q = self.quote(admin, 'starter', 'monthly')
        self.assertEqual(q.status_code, 400, 'a same-plan interval change is never an upgrade quote')

    def test_trial_switch_and_downgrade_paths_unchanged(self):
        biz, admin = self.business('core', 'monthly', status='trialing')
        r = self.client.post('/subscription/change-plan', json={'plan': 'enterprise', 'billing_interval': 'annual'}, headers=self.auth(admin, key=False))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((self.stored(biz).plan, self.stored(biz).billing_interval), ('enterprise', 'annual'))
        biz, admin = self.business('enterprise', 'monthly', elapsed=0.5)
        r = self.client.post('/subscription/change-plan', json={'plan': 'core', 'billing_interval': 'monthly'}, headers=self.auth(admin, key=False))
        self.assertEqual(r.status_code, 409, 'downgrades are scheduled, never immediate')

    # --- 4-6: representative end-to-end amounts and periods ---------------------
    def test_representative_paths_charge_the_quote_and_start_a_fresh_period(self):
        for fp, fi, tp, ti, elapsed, credit in [('core', 'monthly', 'starter', 'monthly', 0.5, 250_000),
                                                ('core', 'annual', 'business', 'annual', 0.5, 2_500_000),
                                                ('business', 'monthly', 'enterprise', 'annual', 0.75, 1_250_000)]:
            with self.subTest(f=(fp, fi), t=(tp, ti)):
                biz, admin = self.business(fp, fi, elapsed=elapsed)
                quote, checkout = self.upgrade(admin, tp, ti)
                self.assertAlmostEqual(quote['unused_credit'] * 100, credit, delta=200)
                self.assertEqual(quote['amount_due_kobo'], KOBO(tp, ti) - round(quote['unused_credit'] * 100))
                self.assertEqual(self.paystack.initialized[checkout['reference']]['amount'], quote['amount_due_kobo'])
                paid_at = datetime.utcnow().replace(microsecond=0)
                self.paystack.pay(checkout['reference'], paid_at=paid_at)
                self.webhook(checkout['reference'])
                sub = self.stored(biz)
                self.assertEqual((sub.plan, sub.billing_interval), (tp, ti))
                self.assertEqual((sub.current_period_start, sub.current_period_end), (paid_at, main.add_billing_interval(paid_at, ti)))
                record = self.db.query(main.PaymentRecord).filter_by(paystack_reference=checkout['reference']).one()
                self.assertEqual((record.status, record.amount_kobo, record.paid_at), ('success', quote['amount_due_kobo'], paid_at))

    # --- 7: abandoned upgrade checkout ------------------------------------------
    def test_abandoned_checkout_is_closed_after_the_payment_window_and_never_blocks_renewal(self):
        biz, admin = self.business('core', 'monthly', elapsed=0.5)
        quote, checkout = self.upgrade(admin, 'starter', 'monthly')
        record = self.db.query(main.PaymentRecord).filter_by(paystack_reference=checkout['reference']).one()
        later = datetime.utcnow() + main.UPGRADE_PAYMENT_WINDOW + timedelta(minutes=1)
        self.assertEqual(main.settle_open_attempt(self.db, record, later), 'failed')
        self.db.commit()
        self.assertEqual(self.db.query(main.PaymentRecord).filter_by(id=record.id).one().status, 'failed')
        self.assertEqual(self.db.query(main.SubscriptionUpgradeQuote).filter_by(quote_reference=quote['quote_reference']).one().status, 'expired')
        self.assertEqual(main.open_renewal_attempts(self.db, biz.id), [], 'nothing left open to hold up the renewal')

    def test_abandoned_checkout_at_paid_through_still_closes(self):
        biz, admin = self.business('core', 'monthly', minutes_left=5)
        quote, checkout = self.upgrade(admin, 'starter', 'monthly')
        record = self.db.query(main.PaymentRecord).filter_by(paystack_reference=checkout['reference']).one()
        at_end = self.stored(biz).current_period_end
        self.assertEqual(main.settle_open_attempt(self.db, record, at_end), 'failed')

    def test_a_new_quote_closes_an_earlier_open_upgrade_checkout(self):
        biz, admin = self.business('core', 'monthly', elapsed=0.5)
        _, first = self.upgrade(admin, 'starter', 'monthly')
        _, second = self.upgrade(admin, 'business', 'monthly')
        self.db.expire_all()
        statuses = {r.paystack_reference: r.status for r in self.db.query(main.PaymentRecord).all()}
        self.assertEqual(statuses[first['reference']], 'failed')
        self.assertEqual(statuses[second['reference']], 'initialized')
        # A late payment on the superseded checkout is kept for review, not applied.
        self.paystack.pay(first['reference'])
        self.webhook(first['reference'])
        self.db.expire_all()
        self.assertEqual(self.db.query(main.PaymentRecord).filter_by(paystack_reference=first['reference']).one().status, 'flagged_verification_mismatch')
        self.assertEqual(self.stored(biz).plan, 'core')

    # --- 8: duplicate confirmation / replay ------------------------------------
    def test_duplicate_confirmation_and_second_checkout_never_create_a_second_period(self):
        biz, admin = self.business('core', 'monthly', elapsed=0.5)
        quote, checkout = self.upgrade(admin, 'starter', 'monthly')
        again = self.checkout(admin, quote['quote_reference'])
        self.assertEqual(again.status_code, 200, again.text)
        self.assertTrue(again.json().get('resumed'))
        self.assertEqual(again.json()['reference'], checkout['reference'])
        self.assertEqual(self.db.query(main.PaymentRecord).filter_by(purpose='subscription_upgrade').count(), 1)
        self.paystack.pay(checkout['reference'])
        self.webhook(checkout['reference'])
        first = self.stored(biz)
        period = (first.current_period_start, first.current_period_end)
        self.assertEqual(self.webhook(checkout['reference']).status_code, 200)
        r = self.client.post('/subscription/checkout/confirm', json={'reference': checkout['reference']}, headers=self.auth(admin, key=False))
        self.assertIn(r.status_code, (200, 409), r.text)
        sub = self.stored(biz)
        self.assertEqual((sub.current_period_start, sub.current_period_end), period)
        self.assertEqual(self.checkout(admin, quote['quote_reference']).status_code, 409, 'a paid quote cannot be paid again')

    # --- 9: source period changes after the quote ------------------------------
    def test_paid_through_change_after_quote_invalidates_it(self):
        biz, admin = self.business('core', 'monthly', elapsed=0.5)
        q = self.quote(admin, 'starter', 'monthly').json()
        sub = self.stored(biz); sub.current_period_end = sub.current_period_end + DAY; self.db.commit()
        r = self.checkout(admin, q['quote_reference'])
        self.assertEqual(r.status_code, 409, r.text)
        self.assertEqual(self.db.query(main.SubscriptionUpgradeQuote).filter_by(quote_reference=q['quote_reference']).one().status, 'invalidated')

    def test_paid_through_change_during_checkout_flags_the_payment(self):
        biz, admin = self.business('core', 'monthly', elapsed=0.5)
        quote, checkout = self.upgrade(admin, 'starter', 'monthly')
        sub = self.stored(biz); sub.current_period_end = sub.current_period_end + DAY; self.db.commit()
        self.paystack.pay(checkout['reference'])
        self.webhook(checkout['reference'])
        self.assertEqual(self.stored(biz).plan, 'core')
        self.assertEqual(self.db.query(main.PaymentRecord).filter_by(paystack_reference=checkout['reference']).one().status, 'flagged_verification_mismatch')

    def test_payment_confirmed_after_the_window_is_kept_for_review(self):
        biz, admin = self.business('core', 'monthly', elapsed=0.3)
        quote, checkout = self.upgrade(admin, 'starter', 'monthly')
        late = datetime.utcnow() + main.UPGRADE_PAYMENT_WINDOW + timedelta(minutes=5)
        tx = self.paystack.pay(checkout['reference'], paid_at=late)
        record = self.db.query(main.PaymentRecord).filter_by(paystack_reference=checkout['reference']).one()
        result = main.reconcile_upgrade_payment(self.db, record, tx, late)
        self.db.commit()
        self.assertEqual(result['status'], 'flagged_verification_mismatch')
        self.assertEqual(self.stored(biz).plan, 'core')

    def test_wrong_amount_from_provider_is_flagged(self):
        biz, admin = self.business('core', 'monthly', elapsed=0.5)
        quote, checkout = self.upgrade(admin, 'starter', 'monthly')
        self.paystack.pay(checkout['reference'], amount=quote['amount_due_kobo'] + 150)   # e.g. a fee added on top
        self.webhook(checkout['reference'])
        self.assertEqual(self.stored(biz).plan, 'core')
        self.assertEqual(self.db.query(main.PaymentRecord).filter_by(paystack_reference=checkout['reference']).one().status, 'flagged_verification_mismatch')

    def test_renewal_after_upgrade_is_anchored_at_the_new_paid_through(self):
        biz, admin = self.business('core', 'monthly', elapsed=0.5)
        quote, checkout = self.upgrade(admin, 'starter', 'annual')
        paid_at = datetime.utcnow().replace(microsecond=0)
        self.paystack.pay(checkout['reference'], paid_at=paid_at)
        self.webhook(checkout['reference'])
        sub = self.stored(biz)
        end = main.add_billing_interval(paid_at, 'annual')
        self.assertEqual(main.renewal_anchor(sub), end)
        self.assertIsNone(main.renewal_slot(end, end - HOUR), 'no automatic charge before the new paid-through')
        self.assertEqual(main.renewal_plan_for(sub), ('starter', 'annual'))
        self.assertIsNotNone(main.early_renewal_refusal(sub, biz, paid_at + DAY), 'no early same-plan renewal after the upgrade')


if __name__ == '__main__':
    unittest.main()
