"""OPS-ACCURACY-001 - every Private Ops number has one definition and one source.

Disposable SQLite, the real /api/platform/* endpoints. Each test builds the
exact records a metric is defined over and checks the number Ops reports
against a count made independently from those records.
"""
import os
os.environ['PYTHON_DOTENV_DISABLED'] = '1'
os.environ['SUPPLY_AI_SKIP_DB_STARTUP_CHECK'] = 'true'
os.environ['DATABASE_URL'] = 'postgresql+psycopg://test:test@127.0.0.1:65432/cauldra_test'
os.environ['SUPPLY_AI_SECRET_KEY'] = 'isolated-test-secret-012345678901234567890123456789'
import json
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient
import main
import storage

DAY = timedelta(days=1)
CURRENT = {"state": "current", "code": ["0043"], "database": ["0043"], "pending": []}


class OpsTestBase(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        main.Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

        def db_dep():
            with self.Session() as session:
                yield session
        main.app.dependency_overrides[main.get_db] = db_dep
        self.owner = main.PlatformOwner(email='owner@example.com', password='x', totp_enabled=True)
        self.db = self.Session()
        self.db.add(self.owner); self.db.commit(); self.db.refresh(self.owner)
        owner = self.owner
        main.app.dependency_overrides[main.get_platform_owner] = lambda: owner
        self.client = TestClient(main.app)
        self.migration = dict(CURRENT)
        self.patches = [
            patch.object(main, 'ensure_fresh_fx_rate', return_value=None),
            patch.object(main, 'read_database_migration_status', side_effect=lambda: dict(self.migration)),
            patch.object(main, 'record_ops_signal', side_effect=self._signal),
            patch.object(main, 'SessionLocal', self.Session),
            patch.object(main, 'gemini_client', None), patch.object(main, 'openai_client', None),
            patch.dict(os.environ, {'RESEND_API_KEY': 're_test_key'}), patch.object(main, 'RESEND_FROM', 'Cauldra <noreply@example.com>'),
        ]
        for p in self.patches:
            p.start()
        self.n = 0
        self.now = datetime.utcnow()

    def _signal(self, key, value):
        main.set_platform_setting(self.db, key, value); self.db.commit()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.client.close(); main.app.dependency_overrides.clear()
        self.db.close(); self.engine.dispose()

    # --- fixtures -------------------------------------------------------------
    def business(self, *, joined_days_ago=20, trial_days_ago=None, status='trialing', plan='starter', interval='monthly',
                 period_days_left=None, cancel_at_period_end=False, grace_days_left=None, users=1, last_active=None, name=None):
        self.n += 1
        joined = self.now - joined_days_ago * DAY
        biz = main.BusinessProfile(business_code=f'OP-{self.n:03d}', company_name=name or f'Ops Biz {self.n}', currency='NGN (₦)',
                                   subscription_plan=plan, trial_started_at=joined, subscription_started_at=joined, email=f'biz{self.n}@example.com')
        self.db.add(biz); self.db.commit()
        tstart = self.now - (trial_days_ago if trial_days_ago is not None else joined_days_ago) * DAY
        sub = main.BusinessSubscription(business_id=biz.id, plan=plan, billing_interval=interval, status=status,
                                        trial_start_at=tstart, trial_end_at=tstart + 14 * DAY, card_verified=True,
                                        cancel_at_period_end=cancel_at_period_end)
        if period_days_left is not None:
            sub.current_period_start = self.now + period_days_left * DAY - 30 * DAY
            sub.current_period_end = self.now + period_days_left * DAY
            sub.next_billing_at = sub.current_period_end
            sub.paid_at = sub.current_period_start
        if grace_days_left is not None:
            sub.grace_period_ends_at = self.now + grace_days_left * DAY
        self.db.add(sub)
        made = []
        for i in range(users):
            u = main.User(username=f'u{self.n}_{i}', email=f'user{self.n}_{i}@example.com', password='x', phone=f'+23480{self.n:04d}{i:03d}',
                          role='admin' if i == 0 else 'staff', business_id=biz.id, firstname=f'First{self.n}', lastname=f'Owner{self.n}',
                          created_at=joined, last_active_at=last_active, email_verified_at=joined)
            self.db.add(u); made.append(u)
        self.db.commit()
        return biz, sub, made

    def pay(self, biz, amount_naira, *, status='success', purpose='subscription', days_ago=1, plan='starter', at=None):
        self.n += 1
        at = at or (self.now - days_ago * DAY)
        self.db.add(main.PaymentRecord(business_id=biz.id, plan=plan, billing_interval='monthly', amount_kobo=int(amount_naira * 100),
                                       paystack_reference=f'ref-{self.n}', status=status, purpose=purpose,
                                       paid_at=at if status == 'success' else None, created_at=at))
        self.db.commit()

    def ai(self, biz, *, success=True, provider='gemini', op='chat', cost=None, credits=2, category=None, days_ago=0.1):
        self.db.add(main.AIUsageLedger(business_id=biz.id, operation_type=op, credits_consumed=credits if success else 0,
                                       billing_period='p', provider=provider, model='m', success=success,
                                       estimated_provider_cost=cost, failure_category=category, created_at=self.now - days_ago * DAY))
        self.db.commit()

    def get(self, path):
        r = self.client.get(path)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def row(self, biz):
        return next(r for r in self.get('/api/platform/businesses?limit=200')['items'] if r['id'] == biz.id)


class OverviewTests(OpsTestBase):
    def test_eight_cards_match_independent_counts(self):
        month_start = datetime(self.now.year, self.now.month, 1)
        a, _, _ = self.business(joined_days_ago=3, last_active=self.now - timedelta(minutes=10))        # trialing, active today (if same day)
        b, _, _ = self.business(joined_days_ago=10, trial_days_ago=10, last_active=self.now - 5 * DAY)   # trialing, ends in 4 days
        c, _, _ = self.business(joined_days_ago=60, status='active', plan='business', period_days_left=12, last_active=self.now - 40 * DAY)
        d, _, _ = self.business(joined_days_ago=90, status='trialing')                                    # trial ended -> expired
        self.pay(c, 50000); self.pay(c, 100, purpose='card_verification'); self.pay(c, 50000, status='failed')
        self.ai(a, cost=0.5); self.ai(a, cost=None)
        ov = self.get('/api/platform/overview')
        biz = ov['businesses']
        self.assertEqual(biz['total'], 4)
        self.assertEqual(biz['new_this_month'], sum(1 for x in (3, 10, 60, 90) if self.now - x * DAY >= month_start))
        self.assertEqual(biz['active_30d'], 2)
        self.assertEqual(biz['trial'], 2)
        self.assertEqual(biz['trial_ending_7d'], 1)
        self.assertEqual(biz['paying'], 1)
        self.assertEqual(biz['paying_by_plan'], {'Premium': 1})
        month_paid = 50000 if (self.now - DAY) >= month_start else 0
        self.assertEqual(ov['revenue']['this_month_naira'], month_paid, 'card verification and failed payments are not revenue')
        self.assertEqual(ov['trial_conversion'], {'converted': 1, 'ended_trials': 2, 'rate_pct': 50.0},
                         'trials still running are not in the denominator')
        self.assertEqual(ov['ai_spend']['this_month_usd'], 0.5)
        self.assertEqual(ov['ai_spend']['unpriced_requests'], 1)
        self.assertEqual(len(ov['growth']), 6)
        self.assertEqual(ov['growth'][-1]['total'], 4)

    def test_growth_range_does_not_change_lifetime_totals(self):
        for d in (5, 40, 200):
            self.business(joined_days_ago=d)
        six, twelve = self.get('/api/platform/overview?months=6'), self.get('/api/platform/overview?months=12')
        self.assertEqual(six['businesses'], twelve['businesses'])
        self.assertEqual(len(twelve['growth']), 12)
        self.assertEqual(six['growth'][-1]['total'], twelve['growth'][-1]['total'], 3)

    def test_needs_attention_counts_open_actionable_alerts(self):
        biz, _, _ = self.business()
        self.pay(biz, 20000, status='failed', days_ago=0.1)
        self.migration = {"state": "behind", "code": ["0043"], "database": ["0042"], "pending": ["0043"]}
        ov = self.get('/api/platform/overview')
        titles = [i['title'] for i in ov['attention']['items']]
        self.assertIn('Database is behind the deployed code', titles)
        self.assertIn('Subscription payments are failing', titles)
        self.assertEqual(ov['attention']['critical'], 1)
        self.assertEqual(ov['attention']['total'], ov['attention']['critical'] + ov['attention']['warning'])


class StatusConsistencyTests(OpsTestBase):
    def test_every_surface_agrees_with_access_enforcement(self):
        cases = {
            'trialing': self.business(trial_days_ago=3),
            'expired': self.business(trial_days_ago=20),
            'active': self.business(status='active', period_days_left=10),
            'past_due': self.business(status='active', period_days_left=-1, grace_days_left=2),   # period ended, not cancelling
            'cancelled': self.business(status='active', period_days_left=-1, cancel_at_period_end=True),
            'expired_after_grace': self.business(status='past_due', period_days_left=-10, grace_days_left=-1),
        }
        subs = self.get('/api/platform/subscriptions?limit=200')
        table = {r['id']: r['subscription_status'] for r in subs['items']}
        for label, (biz, sub, _) in cases.items():
            enforcement = main.subscription_access_state(sub)[0]
            self.assertEqual(self.row(biz)['subscription_status'], enforcement, label)
            self.assertEqual(self.get(f'/api/platform/businesses/{biz.id}')['subscription_status'], enforcement, label)
            self.assertEqual(table[biz.id], enforcement, label)
        counted = {}
        for s in table.values():
            counted[s] = counted.get(s, 0) + 1
        self.assertEqual(subs['by_status'], counted)
        self.assertEqual(subs['by_status'], {'trialing': 1, 'expired': 2, 'active': 1, 'past_due': 1, 'cancelled': 1})

    def test_plan_breakdown_uses_plan_ids_with_public_labels(self):
        self.business(plan='core'); self.business(plan='starter'); self.business(plan='starter')
        subs = self.get('/api/platform/subscriptions')
        self.assertEqual(subs['by_plan'], {'core': 1, 'starter': 2})
        self.assertEqual(subs['by_plan_labels'], {'core': 'Starter', 'starter': 'Business'})


class BusinessesTableTests(OpsTestBase):
    def test_lifecycle_billing_fields(self):
        trial, tsub, _ = self.business(trial_days_ago=11)
        paid, psub, _ = self.business(status='active', period_days_left=9)
        cancelling, csub, _ = self.business(status='active', period_days_left=5, cancel_at_period_end=True)
        expired, esub, _ = self.business(trial_days_ago=30)
        r = self.row(trial)['billing']
        self.assertEqual((r['kind'], r['days_left']), ('trial_ends', 3))
        self.assertEqual(r['date'], main.to_utc_iso(tsub.trial_end_at))
        self.assertEqual(self.row(paid)['billing']['kind'], 'renews')
        self.assertEqual(self.row(paid)['next_billing_at'], main.to_utc_iso(psub.next_billing_at))
        self.assertEqual(self.row(cancelling)['billing']['kind'], 'access_until')
        self.assertIsNone(self.row(cancelling)['next_billing_at'], 'a cancelling subscription has no next billing')
        e = self.row(expired)['billing']
        self.assertEqual((e['kind'], e['date']), ('expired', main.to_utc_iso(esub.trial_end_at)))

    def test_paid_users_last_active_and_health(self):
        biz, _, users = self.business(status='active', period_days_left=10, users=3, last_active=None)
        users[2].disabled = True; self.db.commit()
        self.pay(biz, 20000); self.pay(biz, 20000, days_ago=31); self.pay(biz, 50, purpose='card_verification')
        r = self.row(biz)
        self.assertEqual(r['paid_to_cauldra_naira'], 40000)
        self.assertEqual(r['successful_payments'], 2)
        self.assertEqual((r['user_count'], r['user_count_disabled']), (2, 1))
        self.assertIsNone(r['last_active_at'])
        self.assertIn('never_active', [h['code'] for h in r['health']])
        self.assertNotIn('ai_credits_consumed', r, 'AI columns left the Businesses table')
        nopay, _, _ = self.business(status='active', period_days_left=10, last_active=self.now)
        self.assertIn('subscription_mismatch', [h['code'] for h in self.row(nopay)['health']])
        quiet, _, _ = self.business(trial_days_ago=2, last_active=self.now)
        self.assertEqual(self.row(quiet)['health'], [], 'a healthy business shows no health flags')

    def test_filters_sorts_and_owner_search(self):
        old, _, _ = self.business(joined_days_ago=50, trial_days_ago=40, name='Old Mill', last_active=self.now - 45 * DAY)
        new, _, _ = self.business(joined_days_ago=2, name='New Bakery', last_active=self.now)
        soon, _, _ = self.business(joined_days_ago=12, trial_days_ago=12, name='Soon Ends')
        rich, _, _ = self.business(status='active', period_days_left=20, name='Rich Co', joined_days_ago=30)
        self.pay(rich, 200000)
        ids = lambda qs: [r['id'] for r in self.get('/api/platform/businesses?' + qs)['items']]
        self.assertEqual(ids('sort=newest')[0], new.id)
        self.assertEqual(ids('sort=oldest')[0], old.id)
        self.assertEqual(ids('sort=highest_paid')[0], rich.id)
        self.assertEqual(ids('sort=trial_ending')[0], soon.id)
        self.assertEqual(ids('sort=recently_active')[0], new.id)
        self.assertEqual(ids('status=expired'), [old.id])
        self.assertEqual(ids('health=trial_ending'), [soon.id])
        self.assertEqual(set(ids('activity=never')), {soon.id, rich.id})
        self.assertEqual(ids('activity=inactive_30d'), [old.id])
        owner_email = self.db.query(main.User).filter_by(business_id=soon.id, role='admin').one().email
        self.assertEqual(ids('q=' + owner_email), [soon.id])
        self.assertEqual(ids(f'q=Owner{self.db.query(main.User).filter_by(business_id=rich.id).first().lastname[5:]}'), [rich.id])
        self.assertEqual(ids('q=OP-001'), [old.id])


class BusinessDetailTests(OpsTestBase):
    def test_joined_and_trial_started_are_separate_and_no_merchant_data(self):
        biz, sub, users = self.business(joined_days_ago=30, trial_days_ago=5)
        users[0].created_at = None; self.db.commit()
        self.db.add(main.SaleModel(business_id=biz.id, quantity=3, total_price=999999.0)); self.db.commit()
        self.pay(biz, 100, purpose='card_verification')
        d = self.get(f'/api/platform/businesses/{biz.id}')
        self.assertNotEqual(d['joined_at'], d['trial_start_at'])
        self.assertEqual(d['trial_start_at'], main.to_utc_iso(sub.trial_start_at))
        self.assertIsNone(d['users'][0]['created_at'], 'an unknown join date stays unknown (UI shows Not recorded)')
        self.assertEqual(d['paid_to_cauldra_naira'], 0)
        self.assertEqual(d['payments'][0]['purpose'], 'Card verification (refundable, not revenue)')
        self.assertFalse(d['payments'][0]['counts_as_revenue'])
        body = json.dumps(d)
        for merchant_key in ('total_price', 'sales', 'customers', 'inventory', 'expenses', 'products'):
            self.assertNotIn(f'"{merchant_key}"', body)
        self.assertNotIn('999999', body)


class UsersTests(OpsTestBase):
    def test_summary_definitions(self):
        month_start = datetime(self.now.year, self.now.month, 1)
        today = datetime(self.now.year, self.now.month, self.now.day)
        biz, _, users = self.business(users=4, joined_days_ago=100)
        users[0].last_active_at = self.now                       # active today, used before the window -> returning
        users[1].last_active_at = self.now - 10 * DAY            # active 30d, first session inside window -> not returning
        users[2].last_active_at = self.now - 40 * DAY            # inactive
        users[3].disabled = True; users[3].created_at = None
        self.db.add_all([
            main.PresenceSession(session_id='s-old', business_id=biz.id, user_id=users[0].id, signed_in_at=self.now - 60 * DAY),
            main.PresenceSession(session_id='s-new', business_id=biz.id, user_id=users[1].id, signed_in_at=self.now - 10 * DAY),
        ])
        new_biz, _, _ = self.business(joined_days_ago=0.01, users=1)
        self.db.commit()
        s = self.get('/api/platform/users')['summary']
        self.assertEqual(s['total_users'], 5)
        self.assertEqual(s['disabled_users'], 1)
        self.assertEqual(s['new_this_month'], 1 + (3 if self.now - 100 * DAY >= month_start else 0))
        self.assertEqual(s['join_date_not_recorded'], 1)
        self.assertEqual(s['active_today'], 1 if users[0].last_active_at >= today else 0)
        self.assertEqual(s['active_30d'], 2)
        self.assertEqual(s['returning'], 1, 'returning = active in the window AND a session that began before it')

    def test_filters_search_and_detail(self):
        biz, _, users = self.business(users=2, name='Search Target')
        users[1].disabled = True; self.db.commit()
        ids = lambda qs: [u['id'] for u in self.get('/api/platform/users?' + qs)['items']]
        self.assertEqual(ids('status=disabled'), [users[1].id])
        self.assertEqual(set(ids('q=' + biz.business_code)), {users[0].id, users[1].id})
        self.assertEqual(ids('activity=never&status=active'), [users[0].id])
        d = self.get(f'/api/platform/users/{users[0].id}')
        self.assertEqual(d['memberships'], [{'business_id': biz.id, 'business_name': 'Search Target', 'business_code': biz.business_code,
                                             'role': 'admin', 'status': 'active'}])
        self.assertTrue(d['email_verified'])


class LastActiveTests(OpsTestBase):
    def test_idle_heartbeat_does_not_count_as_activity(self):
        biz, _, users = self.business(trial_days_ago=2)
        del main.app.dependency_overrides[main.get_platform_owner]
        token = main.issue_token(users[0], self.db)
        h = {'Authorization': f'Bearer {token}'}
        self.assertEqual(self.client.post('/presence/heartbeat', json={'activity': False}, headers=h).status_code, 200)
        self.db.expire_all()
        self.assertIsNone(self.db.get(main.User, users[0].id).last_active_at, 'an idle open tab is not activity')
        self.assertEqual(self.client.post('/presence/heartbeat', json={'activity': True}, headers=h).status_code, 200)
        self.db.expire_all()
        self.assertIsNotNone(self.db.get(main.User, users[0].id).last_active_at)


class RevenueTests(OpsTestBase):
    def test_revenue_is_cauldra_subscription_payments_only(self):
        a, _, _ = self.business(status='active', period_days_left=10, plan='starter')
        b, _, _ = self.business(status='active', period_days_left=10, plan='business', interval='annual')
        t, _, _ = self.business(trial_days_ago=2)      # trialing, never paid: not in the average
        self.db.add(main.SaleModel(business_id=a.id, quantity=1, total_price=750000.0)); self.db.commit()
        self.pay(a, 20000, days_ago=0.1); self.pay(b, 30000, days_ago=0.1, plan='business'); self.pay(b, 30000, days_ago=400, plan='business')
        self.pay(a, 100, purpose='card_verification', days_ago=0.1)
        self.pay(a, 20000, status='failed', days_ago=0.1); self.pay(a, 20000, status='initialized', days_ago=0.1)
        self.pay(b, 30000, status='flagged_amount_mismatch', days_ago=0.1)
        r = self.get('/api/platform/revenue?period=custom&start=%s&end=%s' % ((self.now - 2 * DAY).date(), self.now.date()))
        self.assertEqual(r['revenue_naira'], 50000)
        self.assertEqual(r['successful_payments'], 2)
        self.assertEqual(r['paying_businesses'], 2)
        self.assertEqual(r['average_revenue_per_business_naira'], 25000, 'trials are not in the denominator')
        self.assertEqual(r['all_time_revenue_naira'], 80000)
        self.assertEqual({p['plan_label']: p['share_pct'] for p in r['by_plan']}, {'Business': 40.0, 'Premium': 60.0})
        self.assertEqual(r['payment_health'], {'successful': 2, 'pending': 1, 'failed': 1, 'needs_review': 1, 'other': 0})
        self.assertFalse(r['refunds']['available'])
        self.assertEqual(r['refunds']['provenance'], 'UNAVAILABLE')
        self.assertEqual(r['mrr']['naira'], round(20000 + 500000 / 12, 2), 'monthly price + annual price / 12')
        self.assertEqual(r['mrr']['subscriptions'], 2)


class AiTests(OpsTestBase):
    def test_aggregation(self):
        a, _, _ = self.business(status='active', period_days_left=10)
        b, _, _ = self.business()
        self.pay(a, 10000, days_ago=0.1)
        self.ai(a, cost=0.02, credits=2); self.ai(a, cost=0.04, credits=5, op='inventory_insight'); self.ai(a, cost=None, credits=2)
        self.ai(b, success=False, category='rate_limited'); self.ai(b, success=False, category=None)
        self.ai(b, provider='openai', op='invoice_ocr', success=False, category='insufficient_provider_credit')
        with patch.object(main, 'get_effective_usd_ngn_rate', return_value=1500.0):
            d = self.get('/api/platform/ai-usage?period=month')
        s = d['summary']
        self.assertEqual((s['requests'], s['successful'], s['failed']), (6, 3, 3))
        self.assertEqual(s['credits_consumed'], 9)
        self.assertAlmostEqual(s['provider_cost_usd'], 0.06)
        self.assertEqual((s['priced_requests'], s['unpriced_requests']), (2, 1))
        self.assertAlmostEqual(s['average_cost_per_request_usd'], 0.03, msg='average over priced requests only')
        self.assertEqual(s['businesses_using_ai'], 2)
        self.assertEqual(s['cost_pct_of_revenue'], round(0.06 * 1500 / 10000 * 100, 1))
        self.assertEqual(d['providers']['gemini']['failure_rate_pct'], 40.0)
        self.assertEqual({f['reason']: f['count'] for f in d['failures_by_reason']},
                         {'rate_limited': 1, 'not_recorded': 1, 'insufficient_provider_credit': 1})
        self.assertEqual(d['by_business'][0]['business_id'], a.id)

    def test_failure_classification(self):
        class RateLimitError(Exception):
            status_code = 429
        class ClientError(Exception):
            code = 403
        def wrapped(inner):
            try:
                try:
                    raise inner
                except Exception as exc:
                    raise HTTPException(status_code=502, detail='x') from exc
            except HTTPException as outer:
                return outer
        self.assertEqual(main.classify_ai_failure(wrapped(RateLimitError('slow down'))), 'rate_limited')
        self.assertEqual(main.classify_ai_failure(wrapped(ClientError('PERMISSION_DENIED'))), 'provider_auth')
        self.assertEqual(main.classify_ai_failure(wrapped(Exception('Error code: 429 - insufficient_quota'))), 'insufficient_provider_credit')
        self.assertEqual(main.classify_ai_failure(wrapped(TimeoutError('timed out'))), 'timeout')
        self.assertEqual(main.classify_ai_failure(wrapped(ValueError('Empty Gemini response'))), 'malformed_response')
        self.assertEqual(main.classify_ai_failure(HTTPException(status_code=503, detail='AI unavailable')), 'not_configured')
        self.assertEqual(main.classify_ai_failure(HTTPException(status_code=400, detail='bad input')), 'validation_failure')

    def test_run_billable_ai_records_the_reason(self):
        biz, _, users = self.business()
        def boom(usage):
            raise HTTPException(status_code=502, detail='x') from TimeoutError('timed out')
        with self.assertRaises(HTTPException):
            main.run_billable_ai(self.db, users[0], 'chat', 'gemini', 'm', boom)
        row = self.db.query(main.AIUsageLedger).filter_by(business_id=biz.id).one()
        self.assertEqual((row.success, row.failure_category, row.credits_consumed), (False, 'timeout', 0))

    def test_pricing_rejects_near_duplicate_model(self):
        self.assertEqual(self.client.put('/api/platform/ai-pricing', json={'provider': 'gemini', 'model': 'gemini-x', 'input_price_per_1k_usd': 0.1}).status_code, 200)
        r = self.client.put('/api/platform/ai-pricing', json={'provider': 'gemini', 'model': 'Gemini-X', 'input_price_per_1k_usd': 0.2})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self.db.query(main.AIProviderPricing).count(), 1)
        self.assertTrue(self.db.query(main.PlatformAuditLog).filter_by(action='AI_PRICING_UPDATED').count() == 1)


class AlertsTests(OpsTestBase):
    def test_repeats_group_into_one_row_and_resolve_reopen(self):
        biz, _, _ = self.business()
        self.pay(biz, 1, status='failed', days_ago=0.2)
        first = self.get('/api/platform/alerts?state=unresolved')['items']
        self.pay(biz, 1, status='failed', days_ago=0.1)
        second = self.get('/api/platform/alerts?state=unresolved')['items']
        pay_alerts = [a for a in second if a['type'] == 'payments_failed_24h']
        self.assertEqual(len(pay_alerts), 1, 'a repeat never adds a row')
        self.assertEqual(pay_alerts[0]['occurrences'], 2)
        a = pay_alerts[0]
        for key in ('title', 'message', 'impact', 'why_it_matters', 'severity', 'source', 'first_seen', 'last_seen', 'affected_businesses', 'state'):
            self.assertIn(key, a)
        self.assertEqual(a['source'], 'Payments')
        self.assertTrue(a['message'].startswith('2 subscription payment(s) failed'))
        r = self.client.post(f"/api/platform/alerts/{a['id']}/state", json={'state': 'resolved'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.get('/api/platform/alerts?state=resolved')['items'][0]['id'], a['id'])
        self.pay(biz, 1, status='failed', at=datetime.utcnow() + timedelta(seconds=1))   # new evidence after resolving
        reopened = [x for x in self.get('/api/platform/alerts?state=unresolved')['items'] if x['id'] == a['id']]
        self.assertEqual(len(reopened), 1)
        self.assertEqual(self.db.query(main.PlatformAuditLog).filter_by(action='PLATFORM_ALERT_STATE').count(), 1)
        self.assertEqual(len(first), len([x for x in first if x['state'] == 'unresolved']))

    def test_self_clearing_condition_cannot_be_hidden_and_clears_itself(self):
        self.migration = {"state": "behind", "code": ["0043"], "database": ["0042"], "pending": ["0043"]}
        alert = next(a for a in self.get('/api/platform/alerts')['items'] if a['type'] == 'db_migration_behind')
        self.assertEqual(alert['severity'], 'critical')
        self.assertEqual(self.client.post(f"/api/platform/alerts/{alert['id']}/state", json={'state': 'resolved'}).status_code, 409)
        self.migration = dict(CURRENT)
        self.assertEqual(next(a for a in self.get('/api/platform/alerts?state=resolved')['items'] if a['id'] == alert['id'])['state'], 'resolved')

    def test_legacy_alert_rows_map_to_new_fields(self):
        self.db.add(main.PlatformAlert(alert_type='ai_budget_threshold', severity='important', title='Gemini spend at 85% of budget',
                                       message='m', dedup_key='ai_budget_gemini_85_2026-09'))
        self.db.commit()
        a = next(x for x in self.get('/api/platform/alerts')['items'] if x['type'] == 'ai_budget_threshold')
        self.assertEqual((a['severity'], a['state'], a['occurrences'], a['source']), ('high', 'unresolved', 1, 'AI providers'))
        self.assertEqual(self.client.post(f"/api/platform/alerts/{a['id']}/acknowledge").status_code, 200)
        self.assertEqual(next(x for x in self.get('/api/platform/alerts')['items'] if x['id'] == a['id'])['state'], 'watching')

    def test_sentry_is_one_source_and_only_when_configured(self):
        self.assertIsNone(main.sentry_api_settings())
        self.assertEqual(self.get('/api/platform/alerts')['sources']['sentry'], 'not_configured')
        issues = [{'id': '1', 'level': 'error', 'count': '40', 'userCount': 4, 'culprit': 'POST /sales/checkout',
                   'title': 'IntegrityError', 'lastSeen': '2026-09-27T09:00:00Z', 'permalink': 'https://sentry.io/i/1'},
                  {'id': '2', 'level': 'warning', 'count': '1', 'userCount': 1, 'culprit': 'app.js', 'title': 'ResizeObserver loop'}]
        resp = MagicMock(); resp.json.return_value = issues; resp.raise_for_status.return_value = None
        env = {'SENTRY_API_TOKEN': 't', 'SENTRY_ORG_SLUG': 'o', 'SENTRY_PROJECT_SLUG': 'p'}
        with patch.dict(os.environ, env), patch('requests.get', return_value=resp) as get:
            items = self.get('/api/platform/alerts')['items']
        self.assertEqual(get.call_count, 1)
        sentry = {a['title']: a for a in items if a['source'] == 'Sentry'}
        self.assertEqual(sentry['Error in POST /sales/checkout']['severity'], 'high')
        self.assertEqual(sentry['Error in POST /sales/checkout']['occurrences'], 40)
        self.assertEqual(sentry['Error in app.js']['severity'], 'low', 'one-off browser noise stays low')
        ov = self.get('/api/platform/overview')
        self.assertNotIn('Error in app.js', [i['title'] for i in ov['attention']['items']], 'low severity is not an owner action')


class HealthTests(OpsTestBase):
    def setUp(self):
        super().setUp()
        self.tmp = Path(tempfile.mkdtemp())
        self.store = storage.LocalStorage(self.tmp)
        self.patches.append(patch.object(main, 'UPLOAD_STORAGE', self.store)); self.patches[-1].start()
        self.patches.append(patch.object(main, '_ping_database', return_value=None)); self.patches[-1].start()

    def tearDown(self):
        super().tearDown()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_database_health_reflects_migration_state(self):
        h = self.get('/api/platform/system-health')
        self.assertEqual(h['checks']['database']['status'], 'healthy')
        self.assertIn('migrations current', h['checks']['database']['summary'])
        self.migration = {"state": "behind", "code": ["0043"], "database": ["0042"], "pending": ["0043"]}
        h = self.get('/api/platform/system-health')
        self.assertEqual(h['checks']['database']['status'], 'needs_attention')
        self.assertEqual(h['checks']['database']['summary'], 'Connected, but 1 migration is missing')
        with patch.object(main, '_ping_database', side_effect=OSError('refused')):
            self.assertEqual(self.get('/api/platform/system-health')['checks']['database']['status'], 'down')

    def test_every_card_is_fresh_and_timestamped(self):
        h = self.get('/api/platform/system-health')
        self.assertEqual(h['fresh_seconds'], main.OPS_HEALTH_FRESH_SECONDS)
        checked = datetime.fromisoformat(h['checked_at'].replace('Z', '+00:00')).replace(tzinfo=None)
        self.assertLess(abs((datetime.utcnow() - checked).total_seconds()), 5)
        for key, c in h['checks'].items():
            self.assertIn('checked_at', c, key); self.assertIn('provenance', c, key)
        self.assertIsNone(h['counters']['failed_webhooks_24h'], 'not recorded -> Unavailable, never 0')
        self.assertIn('UNAVAILABLE', h['counter_notes']['failed_webhooks_24h'])

    def test_stale_and_unconfigured_signals_are_not_green(self):
        main.set_platform_setting(self.db, 'ops_sweep_last_run_at', (self.now - 5 * DAY).isoformat()); self.db.commit()
        with patch.object(main, 'OPS_PROCESS_STARTED_AT', self.now - 6 * DAY):
            h = self.get('/api/platform/system-health')
        self.assertEqual(h['checks']['background']['status'], 'needs_attention')
        self.assertEqual(h['checks']['ai']['status'], 'not_configured', 'configured is not healthy, unconfigured is not green')
        self.assertEqual(h['checks']['email']['status'], 'unknown', 'no send recorded yet is unknown, not healthy')
        main.set_platform_setting(self.db, 'ops_email_last_failure_at', self.now.isoformat()); self.db.commit()
        self.assertEqual(self.get('/api/platform/system-health')['checks']['email']['status'], 'needs_attention')
        with patch.dict(os.environ, {'RESEND_API_KEY': ''}):
            self.assertEqual(self.get('/api/platform/system-health')['checks']['email']['status'], 'not_configured')

    def test_storage_down_raises_a_critical_alert(self):
        with patch.object(self.store, 'health_check', side_effect=ConnectionError('no route')):
            h = self.get('/api/platform/system-health')
        self.assertEqual(h['checks']['storage']['status'], 'down')
        alert = self.db.query(main.PlatformAlert).filter_by(dedup_key='storage_unreachable').one()
        self.assertEqual((alert.severity, alert.state), ('critical', 'unresolved'))
        self.assertEqual(self.get('/api/platform/system-health')['checks']['storage']['status'], 'healthy')
        self.db.expire_all()
        self.assertEqual(self.db.query(main.PlatformAlert).filter_by(dedup_key='storage_unreachable').one().state, 'resolved')


class InfrastructureTests(HealthTests):
    def test_storage_provenance_and_reconciliation(self):
        biz, _, users = self.business()
        self.store.put_bytes(f'{biz.id}/1-kept.pdf', b'12345', 'application/pdf')
        self.store.put_bytes(f'{biz.id}/9-orphan.pdf', b'123', 'application/pdf')
        self.db.add_all([
            main.StoredUpload(business_id=biz.id, kind='receipt', original_name='a.pdf', storage_key=f'{biz.id}/1-kept.pdf', content_type='application/pdf', size_bytes=5, content_hash='h1'),
            main.StoredUpload(business_id=biz.id, kind='receipt', original_name='b.pdf', storage_key=f'{biz.id}/2-gone.pdf', content_type='application/pdf', size_bytes=7, content_hash='h2'),
        ]); self.db.commit()
        infra = self.get('/api/platform/infrastructure')
        self.assertEqual(infra['storage']['tracked'], {'bytes': 12, 'objects': 2, 'provenance': 'CAULDRA CALCULATED',
                                                       'definition': infra['storage']['tracked']['definition']})
        self.assertIsNone(infra['storage']['provider_inventory'], 'never checked yet -> no invented provider number')
        inv = self.client.post('/api/platform/infrastructure/storage-check').json()
        self.assertEqual((inv['provider_objects'], inv['provider_bytes']), (2, 8))
        self.assertEqual((inv['records_missing_object'], inv['objects_without_record']), (1, 1))
        self.assertEqual(inv['provenance'], 'LIVE PROVIDER')
        self.assertTrue((self.tmp / f'{biz.id}/9-orphan.pdf').exists(), 'the check never deletes anything')
        self.assertEqual(self.get('/api/platform/infrastructure')['storage']['provider_inventory']['checked_at'], inv['checked_at'])
        self.assertEqual(self.db.query(main.PlatformAuditLog).filter_by(action='STORAGE_INVENTORY_CHECK').count(), 1)

    def test_no_connection_internals_and_honest_backups(self):
        infra = self.get('/api/platform/infrastructure')
        body = json.dumps(infra)
        for secret in ('127.0.0.1', '65432', 'cauldra_test', 'test:test', 're_test_key'):
            self.assertNotIn(secret, body)
        self.assertEqual(infra['backups']['status'], 'unavailable')
        self.assertEqual(infra['backups']['provenance'], 'UNAVAILABLE')
        self.assertEqual(infra['email']['sender_domain'], 'example.com')


class AccessTests(OpsTestBase):
    def test_every_ops_endpoint_still_requires_the_platform_owner(self):
        main.app.dependency_overrides.pop(main.get_platform_owner)
        for path in ('/api/platform/overview', '/api/platform/businesses', '/api/platform/businesses/1', '/api/platform/users',
                     '/api/platform/users/1', '/api/platform/subscriptions', '/api/platform/revenue', '/api/platform/ai-usage',
                     '/api/platform/alerts', '/api/platform/system-health', '/api/platform/infrastructure'):
            self.assertEqual(self.client.get(path).status_code, 401, path)
        self.assertEqual(self.client.post('/api/platform/infrastructure/storage-check').status_code, 401)
        self.assertEqual(self.client.post('/api/platform/alerts/1/state', json={'state': 'resolved'}).status_code, 401)
        paths = [r.path for r in main.app.routes if getattr(r, 'include_in_schema', False)]
        self.assertFalse([p for p in paths if p.startswith('/api/platform')])


if __name__ == '__main__':
    unittest.main()
