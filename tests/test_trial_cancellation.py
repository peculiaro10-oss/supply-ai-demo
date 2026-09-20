"""SUB-001 — cancelling a trial must stop the conversion, not repossess the trial.

The audit's finding: cancellation flipped the subscription to "cancelled" on the
spot, so the business lost 11 remaining trial days AND read access to its own
data (402 on products, sales history, expenses, financial summary), while the
confirmation dialog promised only "no charge, data kept".

Disposable SQLite; the subscription gate is exercised through real endpoints.
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

READ_ENDPOINTS = ["/products/", "/sales/history", "/expenses/", "/financial-summary"]


class TrialCancellationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        main.Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

        def db_dep():
            with self.Session() as session:
                yield session
        main.app.dependency_overrides[main.get_db] = db_dep
        self.db = self.Session()
        self.business = main.BusinessProfile(business_code='TR-1', company_name='Trialling Ltd', currency='NGN (₦)',
                                             subscription_plan='business')
        self.other = main.BusinessProfile(business_code='TR-2', company_name='Neighbour Ltd', currency='NGN (₦)')
        self.db.add_all([self.business, self.other]); self.db.commit()
        self.admin = self.user('owner', 'admin', self.business.id)
        self.manager = self.user('manager', 'manager', self.business.id)
        self.staff = self.user('staff', 'staff', self.business.id)
        self.other_admin = self.user('neighbour', 'admin', self.other.id)
        now = datetime.utcnow()
        self.trial_end = now + timedelta(days=11)
        self.sub = main.BusinessSubscription(business_id=self.business.id, plan='business', billing_interval='monthly',
                                             status='trialing', trial_start_at=now - timedelta(days=3),
                                             trial_end_at=self.trial_end, current_period_end=self.trial_end,
                                             card_verified=True)
        self.other_sub = main.BusinessSubscription(business_id=self.other.id, plan='business', billing_interval='monthly',
                                                   status='trialing', trial_start_at=now, trial_end_at=self.trial_end,
                                                   current_period_end=self.trial_end, card_verified=True)
        self.db.add_all([self.sub, self.other_sub]); self.db.commit()
        self.client = TestClient(main.app)
        self.patches = [patch.object(main, 'paystack_fetch_subscription', return_value={}),
                        patch.object(main, 'paystack_disable_subscription', return_value=None)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.client.close(); main.app.dependency_overrides.clear()
        self.db.close(); self.engine.dispose()

    def user(self, username, role, business_id):
        u = main.User(username=username, email=f'{username}@example.com', password='x',
                      phone=f'+23480300000{len(username)}', role=role, business_id=business_id)
        self.db.add(u); self.db.commit(); self.db.refresh(u)
        return u

    def auth(self, user):
        return {'Authorization': f'Bearer {main.issue_token(user, self.db)}'}

    def refreshed(self):
        self.db.expire_all()
        return self.db.query(main.BusinessSubscription).filter_by(business_id=self.business.id).one()

    def cancel(self, user=None):
        return self.client.post('/subscription/trial/cancel', headers=self.auth(user or self.admin))

    # --- the defect itself -------------------------------------------------
    def test_cancelling_keeps_access_for_the_rest_of_the_trial(self):
        r = self.cancel()
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body['status'], 'trialing', 'the trial keeps running; only the conversion is cancelled')
        self.assertTrue(body['cancel_at_period_end'])
        self.assertTrue(body['access_until'].startswith(self.trial_end.strftime('%Y-%m-%d')))
        sub = self.refreshed()
        self.assertEqual(sub.status, 'trialing')
        self.assertTrue(sub.cancel_at_period_end)
        self.assertIsNotNone(sub.cancelled_at)
        for path in READ_ENDPOINTS:
            self.assertNotEqual(self.client.get(path, headers=self.auth(self.admin)).status_code, 402,
                                f'{path} must stay reachable for the rest of the paid-for trial')

    def test_access_ends_when_the_trial_actually_ends(self):
        self.cancel()
        sub = self.refreshed()
        sub.trial_end_at = datetime.utcnow() - timedelta(minutes=1)      # the trial runs out
        self.db.commit()
        r = self.client.get('/products/', headers=self.auth(self.admin))
        self.assertEqual(r.status_code, 402, r.text)
        self.assertIn('trial has ended', r.json()['detail'])
        self.assertEqual(self.refreshed().status, 'expired', 'expiry, not a surprise mid-trial cancellation')

    def test_no_conversion_is_configured_after_cancelling(self):
        self.cancel()
        self.assertTrue(self.refreshed().cancel_at_period_end,
                        'the recurring-charge setup is skipped while cancel_at_period_end is set')

    # --- guards ------------------------------------------------------------
    def test_second_cancellation_is_refused_cleanly(self):
        self.assertEqual(self.cancel().status_code, 200)
        again = self.cancel()
        self.assertEqual(again.status_code, 409)
        self.assertIn('already set to end', again.json()['detail'])

    def test_only_an_admin_can_cancel(self):
        for user in (self.manager, self.staff):
            r = self.cancel(user)
            self.assertEqual(r.status_code, 403, f'{user.role}: {r.text}')
        self.assertFalse(self.refreshed().cancel_at_period_end)

    def test_cancelling_one_business_never_touches_another(self):
        self.cancel()
        self.db.expire_all()
        neighbour = self.db.query(main.BusinessSubscription).filter_by(business_id=self.other.id).one()
        self.assertEqual(neighbour.status, 'trialing')
        self.assertFalse(neighbour.cancel_at_period_end)
        self.assertNotEqual(self.client.get('/products/', headers=self.auth(self.other_admin)).status_code, 402)

    def test_paid_cancellation_still_behaves_as_before(self):
        sub = self.refreshed()
        sub.status = 'active'
        sub.current_period_end = datetime.utcnow() + timedelta(days=20)
        self.db.commit()
        r = self.client.post('/subscription/cancel', headers=self.auth(self.admin))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()['cancel_at_period_end'])
        self.assertEqual(self.refreshed().status, 'active', 'access continues to the end of the paid period')
        self.assertNotEqual(self.client.get('/products/', headers=self.auth(self.admin)).status_code, 402)

    def test_billing_surface_reports_the_cancellation(self):
        self.cancel()
        usage = self.client.get('/subscription/usage', headers=self.auth(self.admin))
        self.assertEqual(usage.status_code, 200, usage.text)
        body = usage.json()
        self.assertTrue(body['cancel_at_period_end'])
        self.assertEqual(body['status'], 'trialing')


if __name__ == '__main__':
    unittest.main(verbosity=2)
