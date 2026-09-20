"""The UPCitemdb 429 cooldown: once the provider says it is rate limited, stop
asking until it says it is willing again.

The free plan is metered per source IP and every request made during a lockout
pushes the provider's own reset further out, so retrying is actively harmful.
No live provider requests: every call is mocked.
"""
import os
os.environ['PYTHON_DOTENV_DISABLED'] = '1'
os.environ['SUPPLY_AI_SKIP_DB_STARTUP_CHECK'] = 'true'
os.environ['DATABASE_URL'] = 'postgresql+psycopg://test:test@127.0.0.1:65432/cauldra_test'
os.environ['SUPPLY_AI_SECRET_KEY'] = 'isolated-test-secret-012345678901234567890123456789'
import time
import unittest
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from unittest.mock import patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient
import main
import upcitemdb_provider as provider

EAN_CACHED = '5000112548167'
EAN_STOCKED = '5901234123457'


class _Resp:
    """The minimum of requests.Response that the provider reads."""

    def __init__(self, status, headers=None, body='{"code":"EXCEED_LIMIT"}'):
        self.status_code = status
        self.headers = headers or {}
        self.text = body
        self.ok = 200 <= status < 300

    def json(self):
        import json
        return json.loads(self.text)


def _hit_body(name='Provider Product'):
    import json
    return json.dumps({'code': 'OK', 'total': 1, 'items': [{'title': name, 'brand': 'ProvBrand', 'size': '330ml'}]})


class CooldownUnitTests(unittest.TestCase):
    def setUp(self):
        provider.clear_cooldown()
        self.addCleanup(provider.clear_cooldown)

    def test_a_429_records_a_cooldown_from_retry_after(self):
        with patch('requests.get', return_value=_Resp(429, {'Retry-After': '600'})):
            out = provider.lookup_upcitemdb_detailed('0049000042566')
        self.assertEqual(out['outcome'], 'temporary_error')
        self.assertIn('cooling down', out['detail'])
        remaining = provider.cooldown_remaining()
        self.assertGreater(remaining, 590)
        self.assertLessEqual(remaining, 601)

    def test_a_fractional_retry_after_is_honoured(self):
        """UPCitemdb has been seen sending 34925.447, which int() would reject."""
        with patch('requests.get', return_value=_Resp(429, {'Retry-After': '34925.447'})):
            provider.lookup_upcitemdb_detailed('0049000042566')
        self.assertGreater(provider.cooldown_remaining(), 34000)

    def test_an_http_date_retry_after_is_honoured(self):
        when = datetime.now(timezone.utc) + timedelta(seconds=1200)
        with patch('requests.get', return_value=_Resp(429, {'Retry-After': format_datetime(when)})):
            provider.lookup_upcitemdb_detailed('0049000042566')
        self.assertGreater(provider.cooldown_remaining(), 1100)

    def test_the_rate_limit_reset_header_is_used_when_retry_after_is_absent(self):
        reset = time.time() + 800
        with patch('requests.get', return_value=_Resp(429, {'X-RateLimit-Reset': str(int(reset))})):
            provider.lookup_upcitemdb_detailed('0049000042566')
        self.assertGreater(provider.cooldown_remaining(), 700)

    def test_a_429_with_no_usable_header_still_cools_down(self):
        with patch('requests.get', return_value=_Resp(429, {})):
            provider.lookup_upcitemdb_detailed('0049000042566')
        self.assertGreaterEqual(provider.cooldown_remaining(), provider._COOLDOWN_MIN_SECONDS)

    def test_a_nonsense_header_cannot_disable_lookups_for_longer_than_a_day(self):
        with patch('requests.get', return_value=_Resp(429, {'Retry-After': '999999999'})):
            provider.lookup_upcitemdb_detailed('0049000042566')
        self.assertLessEqual(provider.cooldown_remaining(), 24 * 60 * 60 + 1)

    def test_a_past_retry_after_falls_back_instead_of_disabling_the_cooldown(self):
        with patch('requests.get', return_value=_Resp(429, {'Retry-After': '-5'})):
            provider.lookup_upcitemdb_detailed('0049000042566')
        self.assertGreaterEqual(provider.cooldown_remaining(), provider._COOLDOWN_MIN_SECONDS)

    def test_a_cooldown_is_never_shortened_by_a_later_smaller_value(self):
        with patch('requests.get', return_value=_Resp(429, {'Retry-After': '3600'})):
            provider.lookup_upcitemdb_detailed('0049000042566')
        long_cooldown = provider.cooldown_remaining()
        with patch('requests.get', return_value=_Resp(429, {'Retry-After': '60'})):
            provider.lookup_upcitemdb_detailed('0049000042566')
        self.assertGreaterEqual(provider.cooldown_remaining(), long_cooldown - 5)

    def test_a_successful_lookup_records_no_cooldown(self):
        with patch('requests.get', return_value=_Resp(200, {}, _hit_body())):
            out = provider.lookup_upcitemdb_detailed('0049000042566')
        self.assertEqual(out['outcome'], 'hit')
        self.assertEqual(provider.cooldown_remaining(), 0)

    def test_a_miss_records_no_cooldown(self):
        with patch('requests.get', return_value=_Resp(200, {}, '{"code":"NOT_FOUND"}')):
            out = provider.lookup_upcitemdb_detailed('0049000042566')
        self.assertEqual(out['outcome'], 'miss')
        self.assertEqual(provider.cooldown_remaining(), 0)

    def test_the_cooldown_expires(self):
        with patch('requests.get', return_value=_Resp(429, {'Retry-After': '600'})):
            provider.lookup_upcitemdb_detailed('0049000042566')
        self.assertGreater(provider.cooldown_remaining(), 0)
        self.assertEqual(provider.cooldown_remaining(now=time.time() + 601), 0)

    def test_the_cooldown_stores_no_product_information(self):
        with patch('requests.get', return_value=_Resp(429, {'Retry-After': '600'})):
            provider.lookup_upcitemdb_detailed('0049000042566')
        self.assertIsInstance(provider._rate_limited_until, float)
        self.assertNotIn('0049000042566', str(provider._rate_limited_until))


class CooldownEndpointTests(unittest.TestCase):
    def setUp(self):
        provider.clear_cooldown()
        self.addCleanup(provider.clear_cooldown)
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        main.Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

        def db_dep():
            with self.Session() as session:
                yield session
        main.app.dependency_overrides[main.get_db] = db_dep
        self.db = self.Session()
        now = datetime.utcnow()
        self.biz_a = main.BusinessProfile(business_code='CD-A', company_name='Alpha', currency='NGN (₦)',
                                          subscription_plan='business')
        self.biz_b = main.BusinessProfile(business_code='CD-B', company_name='Beta', currency='NGN (₦)',
                                          subscription_plan='business')
        self.db.add_all([self.biz_a, self.biz_b]); self.db.commit()
        for biz in (self.biz_a, self.biz_b):
            self.db.add(main.BusinessSubscription(business_id=biz.id, plan='business', billing_interval='monthly',
                                                  status='active', current_period_start=now,
                                                  current_period_end=now + timedelta(days=30), card_verified=True))
        self.db.commit()
        self.admin_a = self.user('cd_alpha', 'admin', self.biz_a.id)
        self.staff_a = self.user('cd_staff', 'staff', self.biz_a.id)
        self.admin_b = self.user('cd_beta', 'admin', self.biz_b.id)
        self.db.add(main.Product(name='Stocked Item', sku='CD-STOCK', barcode=EAN_STOCKED, category='Grains',
                                 business_id=self.biz_a.id, cost_price=100.0, retail_price=150.0, quantity=1,
                                 min_stock_level=1, created_at=now))
        self.db.add(main.GeneralCatalog(barcode=EAN_CACHED, catalog_key=f'barcode:{EAN_CACHED}',
                                        product_name='Already Known', source='upcitemdb'))
        self.db.commit()
        self.client = TestClient(main.app)

    def tearDown(self):
        self.client.close(); main.app.dependency_overrides.clear()
        self.db.close(); self.engine.dispose()

    def user(self, username, role, business_id):
        u = main.User(username=username, email=f'{username}@example.com', password='x',
                      phone=f'+2348031{len(username):04d}0', role=role, business_id=business_id)
        self.db.add(u); self.db.commit(); self.db.refresh(u)
        return u

    def auth(self, user):
        return {'Authorization': f'Bearer {main.issue_token(user, self.db)}'}

    def lookup(self, barcode, user=None):
        return self.client.post('/catalog/barcode-lookup', json={'barcode': barcode},
                                headers=self.auth(user or self.admin_a))

    def test_a_second_lookup_during_the_cooldown_never_reaches_the_provider(self):
        with patch('requests.get', return_value=_Resp(429, {'Retry-After': '600'})) as first:
            r1 = self.lookup('4006381333931')
            self.assertEqual(first.call_count, 1)
        self.assertEqual(r1.json()['source'], 'upcitemdb_unavailable')
        with patch('requests.get', side_effect=AssertionError('cooldown active — must not call the provider')):
            r2 = self.lookup('4006381333932')
        body = r2.json()
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(body['source'], 'upcitemdb_unavailable')
        self.assertEqual(body['upcitemdb_outcome'], 'cooldown')
        self.assertTrue(body['manual_entry'], 'manual entry must remain available')

    def test_the_customer_sees_the_same_fallback_shape_either_way(self):
        with patch('requests.get', return_value=_Resp(429, {'Retry-After': '600'})):
            during_call = self.lookup('4006381333931').json()
        with patch('requests.get', side_effect=AssertionError('must not be called')):
            during_cooldown = self.lookup('4006381333932').json()
        self.assertEqual(set(during_call), set(during_cooldown))
        self.assertEqual(during_call['source'], during_cooldown['source'])
        self.assertEqual(during_call['manual_entry'], during_cooldown['manual_entry'])

    def test_no_provider_internals_are_exposed_to_the_caller(self):
        with patch('requests.get', return_value=_Resp(429, {'Retry-After': '600'})):
            self.lookup('4006381333931')
        with patch('requests.get', side_effect=AssertionError('must not be called')):
            body = self.lookup('4006381333932').json()
        blob = str(body).lower()
        for leak in ('upcitemdb.com', 'exceed_limit', 'x-ratelimit', 'retry-after', 'http 429', 'requests'):
            self.assertNotIn(leak, blob, leak)

    def test_an_own_inventory_hit_ignores_the_cooldown(self):
        with patch('requests.get', return_value=_Resp(429, {'Retry-After': '600'})):
            self.lookup('4006381333931')
        with patch('requests.get', side_effect=AssertionError('own-inventory hit must not touch the provider')):
            body = self.lookup(EAN_STOCKED).json()
        self.assertEqual(body['source'], 'own_inventory')
        self.assertTrue(body['found'])

    def test_a_general_catalog_hit_ignores_the_cooldown(self):
        with patch('requests.get', return_value=_Resp(429, {'Retry-After': '600'})):
            self.lookup('4006381333931')
        with patch('requests.get', side_effect=AssertionError('catalog hit must not touch the provider')):
            body = self.lookup(EAN_CACHED).json()
        self.assertEqual(body['source'], 'cauldra_catalog')
        self.assertEqual(body['product_name'], 'Already Known')

    def test_provider_calls_resume_once_the_cooldown_expires(self):
        with patch('requests.get', return_value=_Resp(429, {'Retry-After': '600'})):
            self.lookup('4006381333931')
        with patch.object(provider, '_rate_limited_until', time.time() - 1):
            with patch('requests.get', return_value=_Resp(200, {}, _hit_body('Back In Business'))) as again:
                body = self.lookup('4006381333933').json()
                self.assertEqual(again.call_count, 1)
        self.assertEqual(body['source'], 'upcitemdb')
        self.assertEqual(body['product_name'], 'Back In Business')

    def test_nothing_is_cached_as_identity_during_a_cooldown(self):
        with patch('requests.get', return_value=_Resp(429, {'Retry-After': '600'})):
            self.lookup('4006381333931')
        with patch('requests.get', side_effect=AssertionError('must not be called')):
            self.lookup('4006381333932')
        rows = self.db.query(main.GeneralCatalog).all()
        self.assertEqual([r.barcode for r in rows], [EAN_CACHED],
                         'a 429, a cooldown and a not-found must never become catalog rows')

    def test_the_cooldown_is_provider_global_not_tenant_scoped(self):
        with patch('requests.get', return_value=_Resp(429, {'Retry-After': '600'})):
            self.lookup('4006381333931', user=self.admin_a)
        with patch('requests.get', side_effect=AssertionError('the limit is per IP, so it applies to every tenant')):
            body = self.lookup('4006381333932', user=self.admin_b).json()
        self.assertEqual(body['upcitemdb_outcome'], 'cooldown')

    def test_the_per_business_allowance_still_works_independently(self):
        with patch.object(main, 'EXTERNAL_LOOKUP_DAILY_LIMIT', 1):
            with patch('requests.get', return_value=_Resp(200, {}, '{"code":"NOT_FOUND"}')):
                first = self.lookup('4006381333931', user=self.admin_a).json()
            self.assertEqual(first['source'], 'not_found')
            with patch('requests.get', side_effect=AssertionError('allowance spent — must not call the provider')):
                spent = self.lookup('4006381333932', user=self.admin_a).json()
            # Business B still holds its own allowance, so its call SHOULD reach the provider.
            with patch('requests.get', return_value=_Resp(200, {}, '{"code":"NOT_FOUND"}')) as b_call:
                other = self.lookup('4006381333933', user=self.admin_b)
                self.assertEqual(b_call.call_count, 1)
        self.assertEqual(spent['upcitemdb_outcome'], 'allowance_spent')
        self.assertEqual(provider.cooldown_remaining(), 0, 'an allowance refusal is not a provider cooldown')
        self.assertEqual(other.json()['source'], 'not_found', 'Business B is unaffected by Business A')

    def test_the_cooldown_is_not_a_plan_or_permission_rule(self):
        """Every role and plan sees the same cooldown: it is an outage, not an entitlement."""
        with patch('requests.get', return_value=_Resp(429, {'Retry-After': '600'})):
            self.lookup('4006381333931')
        with patch('requests.get', side_effect=AssertionError('must not be called')):
            for user in (self.admin_a, self.staff_a, self.admin_b):
                r = self.lookup('4006381333934', user=user)
                self.assertEqual(r.status_code, 200, r.text)
                self.assertEqual(r.json()['upcitemdb_outcome'], 'cooldown')
                self.assertNotIn('plan', r.json()['upcitemdb_detail'].lower())
                self.assertNotIn('upgrade', str(r.json()).lower())


if __name__ == '__main__':
    unittest.main()
