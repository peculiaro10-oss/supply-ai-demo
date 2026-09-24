"""Batch C — billing, subscriptions and staff remediation (launch triage 3–5, 25–36).

Endpoint tests on disposable SQLite, in the same style as
tests/test_batch_b_security.py. Roles: Admin, Manager, Staff (default, granted
and denied), guest; Tenant A vs Tenant B wherever the data is tenant-owned;
plans Starter (id core), Business (id starter), Premium (id business) and
Enterprise; trialing, active and cancelled subscriptions. They execute the
FastAPI handlers and SQLAlchemy persistence; they do not prove PostgreSQL
behaviour or a real Paystack payment (QA / the owner's Paystack TEST pass do).

  PLAN-005  downgrade below usage keeps every record and blocks only growth
  PLAN-007  Price Monitor is plan-gated server-side; records stay readable
  PLAN-008  AI use stays attributed to the plan in force when it happened
  PLAN-001  upgrade prompts name the plan that unlocks a feature, by label
  PLAN-002  no 80% warning on a plan with no AI allowance
  UX-005    stale-price guard on plan changes
  SUB-002/004/005  one subscription-access answer; "will not renew" keeps access
  ACCT-001  only approve / reject resolve an approval request
  X4        the refund list follows sales.refund
  supplier.edit  governs a real edit endpoint
  Static    frontend rules that have no server endpoint (presets, copy, a11y,
            payments bridge, confirmation step)
"""
import os
import tempfile
os.environ['PYTHON_DOTENV_DISABLED'] = '1'
os.environ['SUPPLY_AI_SKIP_DB_STARTUP_CHECK'] = 'true'
os.environ['DATABASE_URL'] = 'postgresql+psycopg://test:test@127.0.0.1:65432/cauldra_test'
os.environ['SUPPLY_AI_SECRET_KEY'] = 'isolated-test-secret-012345678901234567890123456789'
os.environ.setdefault('SUPPLY_AI_UPLOAD_DIR', tempfile.mkdtemp(prefix='cauldra-batchc-'))
import hashlib
import hmac
import json
import re
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
APP_JS = (ROOT / 'frontend' / 'js' / 'app.js').read_text(encoding='utf-8')
PAYMENTS_JS = (ROOT / 'frontend' / 'js' / 'payments.js').read_text(encoding='utf-8')
INDEX_HTML = (ROOT / 'frontend' / 'index.html').read_text(encoding='utf-8')


class BatchCBase(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        main.Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

        def db_dep():
            with self.Session() as session:
                yield session
        main.app.dependency_overrides[main.get_db] = db_dep
        self.db = self.Session()
        self.biz_a = self.business('AA-1111-11', 'Tenant A', plan='business')
        self.biz_b = self.business('BB-2222-22', 'Tenant B', plan='business')
        self.admin = self.user('owner', 'admin', self.biz_a)
        self.manager = self.user('manny', 'manager', self.biz_a)
        self.staff = self.user('stella', 'staff', self.biz_a)
        self.admin_b = self.user('bowner', 'admin', self.biz_b)
        self.client = TestClient(main.app)

    def tearDown(self):
        self.client.close()
        main.app.dependency_overrides.clear()
        self.db.close()
        self.engine.dispose()

    # ---------------------------------------------------------------- fixtures
    def business(self, code, name, plan='business', status='active'):
        biz = main.BusinessProfile(business_code=code, company_name=name, currency='NGN (₦)',
                                   subscription_plan=plan, country_code='NG')
        self.db.add(biz); self.db.commit()
        now = datetime.utcnow()
        self.db.add(main.BusinessSubscription(
            business_id=biz.id, plan=plan, billing_interval='monthly', status=status,
            trial_start_at=now - timedelta(days=1), trial_end_at=now + timedelta(days=13),
            current_period_start=now - timedelta(days=1), current_period_end=now + timedelta(days=29),
            card_verified=True))
        self.db.commit(); self.db.refresh(biz)
        return biz

    def sub(self, biz):
        self.db.expire_all()
        return self.db.query(main.BusinessSubscription).filter_by(business_id=biz.id).one()

    def set_sub(self, biz, **fields):
        sub = self.sub(biz)
        for k, v in fields.items():
            setattr(sub, k, v)
        if 'plan' in fields:
            b = self.db.get(main.BusinessProfile, biz.id); b.subscription_plan = fields['plan']
        self.db.commit()

    def user(self, username, role, biz, **extra):
        u = main.User(username=username, email=f'{username}@example.com', password=main.hash_password(PASSWORD),
                      phone='+2348030000000', role=role, business_id=biz.id, **extra)
        self.db.add(u); self.db.commit(); self.db.refresh(u)
        return u

    def auth(self, user):
        return {'Authorization': f'Bearer {main.issue_token(user, self.db)}'}

    def grant(self, user, *codes):
        self.db.refresh(user)
        user.permission_overrides = json.dumps({code: True for code in codes})
        self.db.commit()

    def product_and_supplier(self, biz, name='Rice'):
        p = main.Product(name=name, sku=f'{name[:3].upper()}-1', category='Food', quantity=5, retail_price=100.0,
                         min_stock_level=1, business_id=biz.id)
        s = main.Supplier(name=f'{name} Wholesale', phone='+2348030000001', business_id=biz.id)
        self.db.add_all([p, s]); self.db.commit(); self.db.refresh(p); self.db.refresh(s)
        return p, s

    def ai_usage(self, biz, credits, when):
        self.db.add(main.AIUsageLedger(business_id=biz.id, user_id=None, operation_type='chat', credits_consumed=credits,
                                       billing_period='p', success=True, created_at=when))
        self.db.commit()

    def usage(self, user):
        r = self.client.get('/subscription/usage', headers=self.auth(user))
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()


# ================================================================== PLAN-007
class PriceMonitorPlanGateTests(BatchCBase):
    def setUp(self):
        super().setUp()
        self.product, self.supplier = self.product_and_supplier(self.biz_a)
        # A source created while the business was on a plan that included it.
        self.source = main.PriceMonitorSource(business_id=self.biz_a.id, supplier_id=self.supplier.id,
                                              product_id=self.product.id, source_type='manual', is_active=True,
                                              last_price=90.0)
        self.db.add(self.source); self.db.commit(); self.db.refresh(self.source)
        self.set_sub(self.biz_a, plan='core')   # now on Starter: price_monitor 0

    def test_starter_keeps_existing_records_readable(self):
        r = self.client.get('/price-monitor', headers=self.auth(self.admin))
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertFalse(body['included'])
        self.assertEqual(body['plan_label'], 'Starter')
        self.assertEqual(body['upgrade_plan_label'], 'Business')
        self.assertEqual([s['id'] for s in body['sources']], [self.source.id])

    def test_starter_refuses_every_price_monitor_action(self):
        h = self.auth(self.admin)
        calls = {
            'create': self.client.post('/price-monitor/sources', headers=h, json={
                'supplier_id': self.supplier.id, 'product_id': self.product.id, 'source_type': 'manual'}),
            'record price': self.client.post(f'/price-monitor/{self.source.id}/price', headers=h, json={'price': 95}),
            'upload list': self.client.post('/price-monitor/upload-price-list', headers=h, json={
                'supplier_id': self.supplier.id, 'product_id': self.product.id, 'file_name': 'p.csv',
                'file_data': 'data:text/csv;base64,UklDRSwxMDAK'}),
        }
        for name, r in calls.items():
            self.assertEqual(r.status_code, 403, f'{name}: {r.text}')
            self.assertIn('not included in your Starter plan', r.json()['detail'])
            self.assertIn('Upgrade to Business', r.json()['detail'])
        self.db.expire_all()
        self.assertEqual(self.db.query(main.PriceMonitorSource).filter_by(business_id=self.biz_a.id).count(), 1)
        self.assertEqual(self.db.query(main.PriceHistory).count(), 0)

    def test_starter_can_still_free_capacity_but_not_reactivate(self):
        h = self.auth(self.admin)
        off = self.client.patch(f'/price-monitor/sources/{self.source.id}', headers=h, json={'is_active': False})
        self.assertEqual(off.status_code, 200, off.text)
        on = self.client.patch(f'/price-monitor/sources/{self.source.id}', headers=h, json={'is_active': True})
        self.assertEqual(on.status_code, 403, on.text)

    def test_granted_staff_on_starter_is_still_refused(self):
        self.grant(self.staff, 'procurement.price_monitor')
        r = self.client.post(f'/price-monitor/{self.source.id}/price', headers=self.auth(self.staff), json={'price': 95})
        self.assertEqual(r.status_code, 403)
        self.assertIn('not included', r.json()['detail'])

    def test_business_plan_allows_price_monitor(self):
        self.set_sub(self.biz_a, plan='starter')   # "Business": price_monitor 5
        h = self.auth(self.admin)
        r = self.client.post(f'/price-monitor/{self.source.id}/price', headers=h, json={'price': 95})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(self.client.get('/price-monitor', headers=h).json()['included'])

    def test_permission_and_tenant_boundaries_hold(self):
        self.set_sub(self.biz_a, plan='starter')
        self.assertEqual(self.client.get('/price-monitor', headers=self.auth(self.staff)).status_code, 403)
        self.assertEqual(self.client.get('/price-monitor').status_code, 401)
        other = self.client.post(f'/price-monitor/{self.source.id}/price', headers=self.auth(self.admin_b), json={'price': 1})
        self.assertEqual(other.status_code, 404)
        self.assertEqual(self.client.get('/price-monitor', headers=self.auth(self.admin_b)).json()['sources'], [])


# ============================================================ PLAN-001 / flags
class PlanFeatureFlagTests(BatchCBase):
    def me(self, user):
        r = self.client.get('/auth/me', headers=self.auth(user))
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def test_every_role_learns_plan_features_and_upgrade_labels(self):
        self.set_sub(self.biz_a, plan='core')
        for user in (self.admin, self.manager, self.staff):
            body = self.me(user)
            self.assertFalse(body['ai_included'])
            self.assertFalse(body['price_monitor_included'])
            self.assertEqual(body['feature_min_plan_labels'], {'ai': 'Business', 'price_monitor': 'Business'})
        self.set_sub(self.biz_a, plan='enterprise')
        body = self.me(self.staff)
        self.assertTrue(body['ai_included']); self.assertTrue(body['price_monitor_included'])

    def test_upgrade_messages_never_name_the_starter_id(self):
        self.assertEqual(main.minimum_plan_label_for('ai'), 'Business')
        self.assertNotIn('available on Starter and above', APP_JS)
        self.assertEqual(len(re.findall(r'featureUpgradeRequired: "[^"]*\{plan\}', APP_JS)), 35)
        self.assertIn('plan: featureMinPlanLabels.ai', APP_JS)


# ============================================================ PLAN-002 / PLAN-008
class AiUsageAccountingTests(BatchCBase):
    def test_no_eighty_percent_warning_on_a_plan_without_ai(self):
        self.set_sub(self.biz_a, plan='core', status='trialing')
        u = self.usage(self.admin)
        self.assertEqual((u['included_ai_credits'], u['used_ai_credits']), (0, 0))
        self.assertIsNone(u['ai_warning'])
        self.assertFalse(u['ai_included'])

    def test_warning_and_overage_still_work_where_ai_is_included(self):
        self.set_sub(self.biz_a, plan='starter')   # 500 included
        self.ai_usage(self.biz_a, 420, datetime.utcnow())
        self.assertEqual(self.usage(self.admin)['ai_warning'], 'warning')
        self.ai_usage(self.biz_a, 180, datetime.utcnow())
        u = self.usage(self.admin)
        self.assertEqual((u['ai_warning'], u['overage_credits']), ('overage', 100))
        self.assertEqual(u['estimated_overage_charge'], 5000)

    def test_downgrade_does_not_reprice_included_usage(self):
        self.set_sub(self.biz_a, plan='business', status='trialing')   # Premium, 2500 included
        self.ai_usage(self.biz_a, 5, datetime.utcnow() - timedelta(minutes=5))
        r = self.client.post('/subscription/change-plan', headers=self.auth(self.admin), json={'plan': 'core'})
        self.assertEqual(r.status_code, 200, r.text)
        u = self.usage(self.admin)
        self.assertEqual(u['plan'], 'core')
        self.assertEqual(u['used_ai_credits'], 5)                 # history intact
        self.assertEqual(u['overage_credits'], 0)
        self.assertEqual(u['estimated_overage_charge'], 0)
        self.assertIsNone(u['ai_warning'])
        self.assertEqual(u['included_by_earlier_plan_credits'], 5)
        audit = self.db.query(main.AuditLog).filter_by(business_id=self.biz_a.id, action='SUBSCRIPTION_PLAN_CHANGED').one()
        self.assertEqual(json.loads(audit.metadata_json), {'from_plan': 'business', 'to_plan': 'core'})

    def test_use_after_a_downgrade_is_measured_against_the_new_plan(self):
        self.set_sub(self.biz_a, plan='starter', status='trialing')   # Business, 500
        self.ai_usage(self.biz_a, 600, datetime.utcnow() - timedelta(minutes=10))   # 100 genuinely over
        self.client.post('/subscription/change-plan', headers=self.auth(self.admin), json={'plan': 'core'})
        u = self.usage(self.admin)
        self.assertEqual(u['overage_credits'], 100)   # only what was over at the time
        self.assertEqual(u['included_by_earlier_plan_credits'], 500)

    def test_upgrade_still_absorbs_earlier_overage(self):
        self.set_sub(self.biz_a, plan='starter', status='trialing')
        self.ai_usage(self.biz_a, 600, datetime.utcnow() - timedelta(minutes=10))
        self.client.post('/subscription/change-plan', headers=self.auth(self.admin), json={'plan': 'business'})
        self.assertEqual(self.usage(self.admin)['overage_credits'], 0)

    def test_legacy_plan_change_without_metadata_never_creates_overage(self):
        self.set_sub(self.biz_a, plan='core', status='trialing')
        self.ai_usage(self.biz_a, 8, datetime.utcnow() - timedelta(minutes=10))
        self.db.add(main.AuditLog(business_id=self.biz_a.id, action='SUBSCRIPTION_PLAN_CHANGED',
                                  description='Subscription changed to Starter (monthly).',
                                  created_at=datetime.utcnow() - timedelta(minutes=5)))
        self.db.commit()
        self.assertEqual(self.usage(self.admin)['overage_credits'], 0)

    def test_without_a_plan_change_the_calculation_is_unchanged(self):
        self.set_sub(self.biz_a, plan='core', status='trialing')
        self.ai_usage(self.biz_a, 8, datetime.utcnow())
        self.assertEqual(self.usage(self.admin)['overage_credits'], 8)


# ============================================================ PLAN-005 / UX-005
class DowngradeOverLimitTests(BatchCBase):
    def setUp(self):
        super().setUp()
        self.set_sub(self.biz_a, plan='enterprise', status='trialing')
        self.extra = [self.user(f'extra{i}', 'staff', self.biz_a) for i in range(3)]   # 4 staff total
        self.disabled = self.user('dormant', 'staff', self.biz_a, disabled=True)

    def downgrade_to_starter(self):
        return self.client.post('/subscription/change-plan', headers=self.auth(self.admin),
                                json={'plan': 'core', 'billing_interval': 'monthly', 'expected_amount_naira': 5000})

    def test_downgrade_keeps_every_record_and_account(self):
        r = self.downgrade_to_starter()
        self.assertEqual(r.status_code, 200, r.text)
        impact = {row['resource']: row for row in r.json()['downgrade_impact']['capacity_impact']}
        self.assertEqual(impact['staff']['status'], 'OVER_LIMIT')
        self.assertEqual(impact['staff']['label'], 'active Staff accounts')
        self.db.expire_all()
        for u in [self.staff, *self.extra]:
            self.assertFalse(self.db.get(main.User, u.id).disabled)
            self.assertEqual(self.client.get('/auth/me', headers=self.auth(u)).status_code, 200)
        u = self.usage(self.admin)
        self.assertIn({'resource': 'staff', 'label': 'active Staff accounts', 'current': 4, 'limit': 2, 'unit': None},
                      u['over_limit_resources'])
        # A Manager sees the same read-only notice (no card data, no actions).
        self.assertTrue(self.usage(self.manager)['over_limit_resources'])

    def test_growth_is_blocked_while_over_the_limit(self):
        self.downgrade_to_starter()
        h = self.auth(self.admin)
        new = self.client.post('/users', headers=h, json={
            'username': 'newbie', 'password': 'abcdef', 'role': 'staff', 'firstname': 'N', 'lastname': 'B',
            'email': 'newbie@example.com', 'phone': '08030000000', 'position': 'Clerk'})
        self.assertEqual(new.status_code, 409, new.text)
        detail = new.json()['detail']
        self.assertEqual(detail['code'], 'PLAN_LIMIT_EXCEEDED')
        self.assertIn('existing data remains available', detail['message'])
        enable = self.client.patch(f'/users/{self.disabled.id}/enable', headers=h)
        self.assertEqual(enable.status_code, 409, enable.text)
        self.db.expire_all()
        self.assertTrue(self.db.get(main.User, self.disabled.id).disabled)

    def test_stale_price_is_refused(self):
        r = self.client.post('/subscription/change-plan', headers=self.auth(self.admin),
                             json={'plan': 'enterprise', 'billing_interval': 'monthly', 'expected_amount_naira': 20000})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self.sub(self.biz_a).plan, 'enterprise')
        wrong = self.client.post('/subscription/change-plan', headers=self.auth(self.admin),
                                 json={'plan': 'core', 'expected_amount_naira': 1})
        self.assertEqual(wrong.status_code, 409)
        self.assertEqual(self.sub(self.biz_a).plan, 'enterprise')

    def test_only_the_admin_changes_or_previews_the_plan(self):
        for user in (self.manager, self.staff):
            self.assertEqual(self.client.post('/subscription/change-plan', headers=self.auth(user),
                                              json={'plan': 'core'}).status_code, 403)
            self.assertEqual(self.client.get('/subscription/downgrade-impact?plan=core',
                                             headers=self.auth(user)).status_code, 403)
        self.assertEqual(self.client.post('/subscription/change-plan', json={'plan': 'core'}).status_code, 401)
        self.assertEqual(self.sub(self.biz_a).plan, 'enterprise')

    def test_malformed_plan_change(self):
        h = self.auth(self.admin)
        self.assertEqual(self.client.post('/subscription/change-plan', headers=h, json={'plan': 'platinum'}).status_code, 400)
        self.assertEqual(self.client.post('/subscription/change-plan', headers=h, json={}).status_code, 422)
        self.assertEqual(self.client.get('/subscription/downgrade-impact?plan=platinum', headers=h).status_code, 400)

    def test_tenant_b_is_unaffected(self):
        self.downgrade_to_starter()
        self.assertEqual(self.sub(self.biz_b).plan, 'business')
        self.assertEqual(self.usage(self.admin_b)['over_limit_resources'], [])


# ============================================================ SUB-002/004/005
class SubscriptionStateTests(BatchCBase):
    SECRET = 'sk_test_batch_c_only'

    def webhook(self, event, data):
        raw = json.dumps({'event': event, 'data': data}).encode()
        sig = hmac.new(self.SECRET.encode(), raw, hashlib.sha512).hexdigest()
        with patch.object(main, 'PAYSTACK_SECRET_KEY', self.SECRET):
            return self.client.post('/webhooks/paystack', content=raw,
                                    headers={'x-paystack-signature': sig, 'Content-Type': 'application/json'})

    def test_blocked_state_is_one_message_for_every_role(self):
        self.set_sub(self.biz_a, status='cancelled')
        for user in (self.admin, self.manager, self.staff):
            me = self.client.get('/auth/me', headers=self.auth(user)).json()
            self.assertEqual(me['subscription_status'], 'cancelled')
            self.assertEqual(me['subscription_blocked_message'], main.SUBSCRIPTION_BLOCKED_MESSAGES['cancelled'])
        products = self.client.get('/products/', headers=self.auth(self.staff))
        self.assertEqual(products.status_code, 402)
        self.assertEqual(products.json()['detail'], main.SUBSCRIPTION_BLOCKED_MESSAGES['cancelled'])
        # Tenant B is not affected by Tenant A's state.
        self.assertIsNone(self.client.get('/auth/me', headers=self.auth(self.admin_b)).json()['subscription_blocked_message'])

    def test_state_read_is_side_effect_free(self):
        self.set_sub(self.biz_a, status='trialing', trial_end_at=datetime.utcnow() - timedelta(minutes=1))
        status, message = main.subscription_access_state(self.sub(self.biz_a))
        self.assertEqual(status, 'expired'); self.assertTrue(message)
        self.assertEqual(self.sub(self.biz_a).status, 'trialing')   # nothing written

    def test_will_not_renew_keeps_access_until_the_period_ends(self):
        self.set_sub(self.biz_a, paystack_subscription_code='SUB_a', paystack_customer_code='CUS_a')
        r = self.webhook('subscription.not_renew', {'subscription_code': 'SUB_a', 'customer': {'customer_code': 'CUS_a'}})
        self.assertEqual(r.status_code, 200, r.text)
        sub = self.sub(self.biz_a)
        self.assertEqual(sub.status, 'active')
        self.assertTrue(sub.cancel_at_period_end)
        self.assertEqual(self.client.get('/products/', headers=self.auth(self.staff)).status_code, 200)

    def test_cancelled_period_end_is_not_a_failed_payment(self):
        self.set_sub(self.biz_a, cancel_at_period_end=True, current_period_end=datetime.utcnow() - timedelta(minutes=1))
        self.assertEqual(self.client.get('/products/', headers=self.auth(self.staff)).status_code, 402)
        self.assertEqual(self.sub(self.biz_a).status, 'cancelled')
        types = {n.type for n in self.db.query(main.Notification).filter_by(business_id=self.biz_a.id)}
        self.assertNotIn('SUBSCRIPTION_PAYMENT_FAILED', types)
        self.assertIn('SUBSCRIPTION_ENDED', types)

    def test_scheduled_downgrade_disable_event_is_not_a_cancellation(self):
        self.set_sub(self.biz_a, paystack_subscription_code='SUB_old', paystack_customer_code='CUS_a',
                     pending_downgrade_plan='starter', pending_downgrade_billing_interval='monthly')
        self.webhook('subscription.disable', {'subscription_code': 'SUB_old', 'customer': {'customer_code': 'CUS_a'}})
        sub = self.sub(self.biz_a)
        self.assertEqual(sub.status, 'active'); self.assertFalse(sub.cancel_at_period_end)

    def test_ended_subscription_is_still_cancelled(self):
        self.set_sub(self.biz_a, paystack_subscription_code='SUB_a', paystack_customer_code='CUS_a',
                     current_period_end=datetime.utcnow() - timedelta(days=1))
        self.webhook('subscription.disable', {'subscription_code': 'SUB_a', 'customer': {'customer_code': 'CUS_a'}})
        self.assertEqual(self.sub(self.biz_a).status, 'cancelled')

    def test_cancel_trial_control_is_not_offered_after_cancelling(self):
        # SUB-003: the control exists only for a trial that has not been cancelled.
        self.assertIn("usage.status === 'trialing' && !usage.cancel_at_period_end", APP_JS)


# ================================================================== ACCT-001
class ApprovalResolutionTests(BatchCBase):
    def setUp(self):
        super().setUp()
        self.req = main.AccountActionRequest(business_id=self.biz_a.id, requested_by_id=self.manager.id,
                                             target_user_id=self.staff.id, target_username=self.staff.username,
                                             action='disable', status='PENDING')
        self.db.add(self.req); self.db.commit(); self.db.refresh(self.req)

    def status(self):
        self.db.expire_all()
        return self.db.get(main.AccountActionRequest, self.req.id).status

    def test_unknown_resolution_is_refused_and_changes_nothing(self):
        for bad in ('banana', 'APPROVE', 'rejected', '%20'):
            r = self.client.post(f'/account-action-requests/{self.req.id}/{bad}', headers=self.auth(self.admin))
            self.assertEqual(r.status_code, 400, f'{bad}: {r.text}')
        self.assertEqual(self.status(), 'PENDING')
        self.assertEqual(self.db.query(main.AuditLog).filter(main.AuditLog.action.like('ACCOUNT_REQUEST_%')).count(), 0)
        ok = self.client.post(f'/account-action-requests/{self.req.id}/approve', headers=self.auth(self.admin))
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertEqual(self.status(), 'APPROVED')
        self.assertTrue(self.db.get(main.User, self.staff.id).disabled)

    def test_reject_still_rejects(self):
        r = self.client.post(f'/account-action-requests/{self.req.id}/reject', headers=self.auth(self.admin))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.status(), 'REJECTED')

    def test_only_this_business_admin_resolves(self):
        self.assertEqual(self.client.post(f'/account-action-requests/{self.req.id}/approve', headers=self.auth(self.manager)).status_code, 403)
        self.assertEqual(self.client.post(f'/account-action-requests/{self.req.id}/approve', headers=self.auth(self.admin_b)).status_code, 404)
        self.assertEqual(self.status(), 'PENDING')


# ============================================================== supplier.edit
class SupplierEditTests(BatchCBase):
    def setUp(self):
        super().setUp()
        _, self.supplier = self.product_and_supplier(self.biz_a)

    def patch(self, user, body):
        return self.client.patch(f'/suppliers/{self.supplier.id}', headers=self.auth(user) if user else {}, json=body)

    def test_permission_is_no_longer_reserved(self):
        self.assertNotIn('reserved', main.PERMISSIONS['supplier.edit'])

    def test_admin_and_manager_edit_by_default(self):
        r = self.patch(self.admin, {'name': 'Rice Traders', 'contact_email': 'sales@rice.example', 'phone': '+2348031111111'})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()['name'], r.json()['contact_email']), ('Rice Traders', 'sales@rice.example'))
        self.assertEqual(self.patch(self.manager, {'lead_time_days': 5}).status_code, 200)
        self.assertEqual(self.db.query(main.AuditLog).filter_by(action='SUPPLIER_UPDATED').count(), 2)

    def test_staff_needs_the_grant(self):
        self.assertEqual(self.patch(self.staff, {'name': 'Nope'}).status_code, 403)
        self.grant(self.staff, 'supplier.edit')
        self.assertEqual(self.patch(self.staff, {'name': 'Granted Name'}).status_code, 200)

    def test_tenant_and_guest_boundaries(self):
        self.assertEqual(self.patch(self.admin_b, {'name': 'Hijack'}).status_code, 404)
        self.assertEqual(self.patch(None, {'name': 'Guest'}).status_code, 401)
        self.db.expire_all()
        self.assertEqual(self.db.get(main.Supplier, self.supplier.id).name, 'Rice Wholesale')

    def test_malformed_edits_are_refused(self):
        for body in ({}, {'name': 'x'}, {'contact_email': 'not-an-email'}, {'phone': '  '},
                     {'lead_time_days': 400}, {'lead_time_days': -1}):
            self.assertEqual(self.patch(self.admin, body).status_code, 400, body)
        self.assertEqual(self.patch(self.admin, {'lead_time_days': 'soon'}).status_code, 422)
        self.assertEqual(self.patch(self.admin, {'contact_email': ''}).status_code, 200)   # clearing is allowed


# ======================================================================== X4
class RefundEntryTests(BatchCBase):
    def test_refund_list_follows_sales_refund(self):
        self.assertEqual(self.client.get('/sales/transactions', headers=self.auth(self.staff)).status_code, 403)
        self.assertEqual(self.client.get('/sales/transactions', headers=self.auth(self.manager)).status_code, 200)
        self.grant(self.staff, 'sales.refund')
        self.assertEqual(self.client.get('/sales/transactions', headers=self.auth(self.staff)).status_code, 200)

    def test_frontend_offers_transactions_only_with_the_permission(self):
        self.assertEqual(APP_JS.count('onclick="openRefundTransactionsModal('), 2)
        self.assertIn("const canRefund = hasPermission('sales.refund');", APP_JS)
        self.assertIn("const transactionsToggle = hasPermission('sales.refund')", APP_JS)
        self.assertNotIn('Staff are explicitly authorized to create refunds', APP_JS)


# ============================================================ static frontend
class FrontendRuleTests(unittest.TestCase):
    def presets(self):
        block = re.search(r'const JOB_PRESETS = \{(.*?)\n        \};', APP_JS, re.S).group(1)
        return {k: set(re.findall(r'"([a-z_]+\.[a-z_]+)"', v)) for k, v in re.findall(r'(\w+): \{ label: "[^"]+", grants: \[([^\]]*)\]', block)}

    def test_presets_keep_staff_baseline_defaults(self):
        presets = self.presets()
        self.assertEqual(set(presets), {'general_staff', 'sales', 'inventory_warehouse', 'procurement', 'finance'})
        self.assertIn('expenses.view', presets['finance'])   # PRESET-002
        preserved = set(re.findall(r'"([a-z_]+\.[a-z_]+)"', re.search(r'PRESET_PRESERVED_CODES = new Set\(\[([^\]]*)\]', APP_JS).group(1)))
        self.assertEqual(preserved, {'expenses.view', 'business_day.manage', 'ai.use'})
        staff_defaults = {c for c, meta in main.PERMISSIONS.items() if meta.get('staff')}
        self.assertTrue(preserved <= staff_defaults)
        self.assertIn('if (PRESET_PRESERVED_CODES.has(cb.dataset.permissionCode) && !grantSet.has(cb.dataset.permissionCode)) return;', APP_JS)
        # The approved narrowing (D13) is unchanged: specialists still omit add_product.
        for key in ('sales', 'procurement', 'finance'):
            self.assertNotIn('inventory.add_product', presets[key])

    def test_add_employee_quotes_the_temporary_password_rule(self):
        self.assertIn('data-i18n="team.tempPasswordRuleHint">At least 6 characters', INDEX_HTML)
        self.assertNotIn('data-i18n="team.passwordHint"', INDEX_HTML)
        self.assertRegex(APP_JS, r'tempPasswordRuleHint: "At least 6 characters')
        self.assertEqual(main.validate_temp_password_strength('abcdef'), None)

    def test_billing_toggle_announces_its_state(self):
        self.assertIn('aria-pressed="true"', INDEX_HTML)
        self.assertIn("monthlyBtn.setAttribute('aria-pressed', String(billingIntervalChoice === 'monthly'))", APP_JS)
        self.assertIn('role="group" aria-label="Billing interval"', INDEX_HTML)

    def test_plan_cards_confirm_and_say_what_they_do(self):
        cards = re.search(r'function renderBillingPlanCards\(\) \{(.*?)\n        \}\n', APP_JS, re.S).group(1)
        self.assertNotIn('switchPlanImmediate(', cards)          # UX-005: no one-click switch
        self.assertNotIn("onclickFn = `subscribeNow(", cards)
        self.assertIn('openPlanChangeConfirm(', cards)
        self.assertNotRegex(cards, r'<button[^>]*>\$\{formatNaira')   # UX-006: price is not the label
        self.assertIn('id="plan-change-confirm-modal"', INDEX_HTML)
        self.assertIn("billing-admin-only-note", cards + APP_JS)      # UX-001

    def test_one_subscription_answer_for_every_module(self):
        gate = re.search(r'function checkFeatureAccess\(featureName, callback\) \{(.*?)\n        \}\n', APP_JS, re.S).group(1)
        self.assertLess(gate.index('subscriptionBlockedMessage'), gate.index('callback();'))
        self.assertIn('if (noteSubscriptionResponse(response, data))', APP_JS)
        sales = re.search(r'async function loadDailySales\(\)\{(.*?)No Business Day is currently open', APP_JS, re.S).group(1)
        self.assertIn('if (subscriptionBlockedMessage)', sales)
        self.assertIn('} else if (subscriptionBlockedMessage) {', APP_JS)   # inventory empty state
        self.assertNotIn('t("businessBrain.upgradeToUnlock")', APP_JS)

    def test_payments_use_the_native_bridge_plugins(self):
        self.assertNotIn("window.Capacitor.registerPlugin('InAppBrowser')", PAYMENTS_JS)
        self.assertNotIn("window.Capacitor.registerPlugin('App')", PAYMENTS_JS)
        self.assertIn("return capacitor.Plugins?.[name] || null;", PAYMENTS_JS)
        self.assertIn("el('payment-check').addEventListener('click',()=>verify(false));", PAYMENTS_JS)
        self.assertNotIn('Do not pay again.', PAYMENTS_JS)


if __name__ == '__main__':
    unittest.main()
