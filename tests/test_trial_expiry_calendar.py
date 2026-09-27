"""TRIAL-EXPIRY-001 — a 14-day trial ends on absolute calendar time, and Cauldra Ops
must say so even for a business that never comes back.

Found on Ops: businesses that joined weeks earlier still read TRIALING while a
frequently used one read EXPIRED. Cause: enforcement compares the server clock
with the stored trial_end_at on every signed-in request and writes "expired" at
that moment, but Ops (list, detail, overview count, status breakdown) printed the
STORED status. A business nobody signed in to after its trial ended was blocked
the moment anyone tried, yet stayed "trialing" in Ops indefinitely. Activity never
moves the trial dates; it only triggers the write.

Disposable SQLite; real endpoints.
"""
import os
os.environ['PYTHON_DOTENV_DISABLED'] = '1'
os.environ['SUPPLY_AI_SKIP_DB_STARTUP_CHECK'] = 'true'
os.environ['DATABASE_URL'] = 'postgresql+psycopg://test:test@127.0.0.1:65432/cauldra_test'
os.environ['SUPPLY_AI_SECRET_KEY'] = 'isolated-test-secret-012345678901234567890123456789'
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient
import main

DAY = timedelta(days=1)


class TrialExpiryCalendarTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        main.Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

        def db_dep():
            with self.Session() as session:
                yield session
        main.app.dependency_overrides[main.get_db] = db_dep
        main.app.dependency_overrides[main.get_platform_owner] = lambda: object()
        self.db = self.Session()
        self.client = TestClient(main.app)
        self.patches = [patch.object(main, 'ensure_fresh_fx_rate', return_value=None)]
        for p in self.patches:
            p.start()
        self.n = 0

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.client.close(); main.app.dependency_overrides.clear()
        self.db.close(); self.engine.dispose()

    # --- fixtures ----------------------------------------------------------
    def business(self, started_days_ago, plan='starter', status='trialing'):
        """A business whose trial started `started_days_ago` days ago (14 calendar days long)."""
        self.n += 1
        start = datetime.utcnow() - timedelta(days=started_days_ago)
        biz = main.BusinessProfile(business_code=f'TX-{self.n}', company_name=f'Trial {self.n} Ltd', currency='NGN (₦)',
                                   subscription_plan=plan, trial_started_at=start, subscription_started_at=start)
        self.db.add(biz); self.db.commit()
        end = start + timedelta(days=main.PLAN_CONFIG[plan]['trial_days'])
        sub = main.BusinessSubscription(business_id=biz.id, plan=plan, billing_interval='monthly', status=status,
                                        trial_start_at=start, trial_end_at=end, current_period_start=start,
                                        current_period_end=end, next_billing_at=end, card_verified=True)
        admin = main.User(username=f'admin{self.n}', email=f'admin{self.n}@example.com', password='x',
                          phone=f'+2348030000{self.n:03d}', role='admin', business_id=biz.id)
        self.db.add_all([sub, admin]); self.db.commit(); self.db.refresh(admin)
        return biz, admin

    def auth(self, user):
        return {'Authorization': f'Bearer {main.issue_token(user, self.db)}'}

    def stored(self, biz):
        self.db.expire_all()
        return self.db.query(main.BusinessSubscription).filter_by(business_id=biz.id).one()

    def ops_row(self, biz):
        rows = self.client.get('/api/platform/businesses?limit=200').json()['items']
        return next(r for r in rows if r['id'] == biz.id)

    def ops_detail(self, biz):
        return self.client.get(f'/api/platform/businesses/{biz.id}').json()

    # --- A / J / D: calendar time, visited or not --------------------------
    def test_A_thirteen_day_old_trial_is_active_everywhere(self):
        biz, admin = self.business(13)
        self.assertEqual(self.client.get('/products/', headers=self.auth(admin)).status_code, 200)
        self.assertEqual(self.stored(biz).status, 'trialing')
        self.assertEqual(self.ops_row(biz)['subscription_status'], 'trialing')
        self.assertTrue(self.ops_row(biz)['is_trial'])

    def test_B_J_never_revisited_trial_older_than_14_days_reads_expired_in_ops(self):
        biz, admin = self.business(15)          # nobody has signed in since sign-up
        self.assertEqual(self.stored(biz).status, 'trialing', 'fixture: the stored row was never touched')
        row, detail = self.ops_row(biz), self.ops_detail(biz)
        self.assertEqual(row['subscription_status'], 'expired', 'Ops must not show TRIALING after the trial ended')
        self.assertFalse(row['is_trial'])
        self.assertEqual(detail['subscription_status'], 'expired')
        self.assertEqual(self.stored(biz).status, 'trialing', 'Ops reads never write')

    def test_D_inactivity_does_not_pause_the_trial(self):
        biz, admin = self.business(14.01)       # just past 14 days, no activity at all
        r = self.client.get('/products/', headers=self.auth(admin))
        self.assertEqual(r.status_code, 402, r.text)
        self.assertIn('trial has ended', r.json()['detail'])
        self.assertEqual(self.stored(biz).status, 'expired')

    # --- C / E / F: activity, sessions and reads never move the dates -------
    def test_C_E_F_activity_logins_session_and_plan_reads_do_not_move_the_trial(self):
        biz, admin = self.business(10)
        before = self.stored(biz)
        start, end = before.trial_start_at, before.trial_end_at
        for _ in range(5):
            h = self.auth(admin)                                  # a fresh sign-in token each time
            for path in ('/auth/me', '/products/', '/subscription/usage', '/plans', '/business-profile/', '/users/me/profile'):
                self.assertNotEqual(self.client.get(path, headers=h).status_code, 402, path)
            self.client.post('/presence/heartbeat', headers=h)
        after = self.stored(biz)
        self.assertEqual((after.trial_start_at, after.trial_end_at, after.status), (start, end, 'trialing'))
        self.assertEqual(self.client.get('/auth/me', headers=self.auth(admin)).json().get('subscription_status'), 'trialing')

    def test_C_frequently_used_account_still_expires_at_14_days(self):
        biz, admin = self.business(10)
        for _ in range(10):
            self.client.get('/products/', headers=self.auth(admin))
        sub = self.stored(biz)                                    # the calendar moves on 5 days
        sub.trial_start_at -= 5 * DAY; sub.trial_end_at -= 5 * DAY; self.db.commit()
        self.assertEqual(self.client.get('/products/', headers=self.auth(admin)).status_code, 402)
        self.assertEqual(self.ops_row(biz)['subscription_status'], 'expired')

    # --- G: every tier gets the same 14 calendar days -----------------------
    def test_G_every_plan_tier_has_a_14_day_trial(self):
        for plan, cfg in main.PLAN_CONFIG.items():
            self.assertEqual(cfg['trial_days'], 14, plan)
        for plan in main.PLAN_CONFIG:
            biz, _ = self.business(15, plan=plan)
            s = self.stored(biz)
            self.assertEqual(s.trial_end_at - s.trial_start_at, timedelta(days=14), plan)
            self.assertEqual(self.ops_row(biz)['subscription_status'], 'expired', plan)

    def test_G_backfilled_subscription_is_14_calendar_days_from_the_original_start(self):
        start = datetime.utcnow() - timedelta(days=20)
        biz = main.BusinessProfile(business_code='TX-legacy', company_name='Legacy Ltd', currency='NGN (₦)',
                                   subscription_plan='business', trial_started_at=start, subscription_started_at=start)
        self.db.add(biz); self.db.commit()
        sub = main.get_or_create_subscription(self.db, biz)
        self.assertEqual((sub.trial_start_at, sub.trial_end_at, sub.status), (start, start + timedelta(days=14), 'expired'))

    # --- H: Ops agrees with enforcement --------------------------------------
    def test_H_ops_status_matches_access_enforcement(self):
        cases = [self.business(3), self.business(13.9), self.business(14.1), self.business(40)]
        for biz, admin in cases:
            ops = self.ops_row(biz)['subscription_status']            # read BEFORE any customer request
            code = self.client.get('/products/', headers=self.auth(admin)).status_code
            self.assertEqual(ops == 'trialing', code == 200, f'{biz.company_name}: Ops {ops} vs access {code}')
            self.assertEqual(self.ops_row(biz)['subscription_status'], ops, 'unchanged by the customer request')

    def test_H_ops_overview_and_breakdown_count_effective_status(self):
        self.business(3); self.business(20); self.business(30)
        overview = self.client.get('/api/platform/overview').json()
        self.assertEqual(overview['businesses']['trial'], 1)
        breakdown = self.client.get('/api/platform/subscriptions').json()
        self.assertEqual(breakdown['by_status'].get('trialing'), 1)
        self.assertEqual(breakdown['by_status'].get('expired'), 2)
        self.assertEqual(breakdown['total'], 3)

    # --- I: boundary is an absolute UTC instant --------------------------------
    def test_I_boundary_is_the_absolute_trial_end_instant(self):
        end = datetime(2026, 9, 15, 23, 30, 0)                   # late evening UTC = next day in Lagos
        sub = main.BusinessSubscription(status='trialing', trial_start_at=end - timedelta(days=14), trial_end_at=end)
        self.assertEqual(main.subscription_access_state(sub, now=end - timedelta(seconds=1))[0], 'trialing')
        self.assertEqual(main.subscription_access_state(sub, now=end)[0], 'expired')
        self.assertEqual(main.subscription_access_state(sub, now=end + timedelta(seconds=1))[0], 'expired')
        self.assertEqual(main.effective_subscription_status(sub, now=end - timedelta(seconds=1)), 'trialing')
        self.assertEqual(main.effective_subscription_status(sub, now=end), 'expired')
        biz, _ = self.business(15)
        iso = self.ops_detail(biz)['trial_end_at']
        self.assertTrue(iso.endswith('Z') or iso.endswith('+00:00'), iso)


if __name__ == '__main__':
    unittest.main()
