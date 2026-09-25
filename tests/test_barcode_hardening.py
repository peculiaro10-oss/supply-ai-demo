"""Barcode hardening: an editable product barcode, and a per-business allowance
for the shared free provider quota.

No live provider requests: UPCitemdb is mocked everywhere. SQLite + TestClient,
so these run in the default suite (unlike the PostgreSQL-backed catalog
scenarios, which still skip without TEST_POSTGRES_ADMIN_URL).
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

EAN_A = '5901234123457'
EAN_B = '4006381333931'
EAN_C = '0049000042566'


def _hit(name='Provider Product', brand='ProvBrand', size='330ml'):
    return {'outcome': 'hit', 'detail': 'ok', 'http_status': 200,
            'identity': {'barcode': EAN_C, 'product_name': name, 'brand': brand, 'size': size}}


def _miss():
    return {'outcome': 'miss', 'identity': None, 'detail': 'no items in response', 'http_status': 200}


def _rate_limited():
    return {'outcome': 'temporary_error', 'identity': None, 'detail': 'HTTP 429 (rate limited)', 'http_status': 429}


class BarcodeHardeningTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        main.Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

        def db_dep():
            with self.Session() as session:
                yield session
        main.app.dependency_overrides[main.get_db] = db_dep
        self.db = self.Session()
        now = datetime.utcnow()
        self.biz_a = main.BusinessProfile(business_code='BC-A', company_name='Alpha Stores', currency='NGN (₦)',
                                          subscription_plan='business')
        self.biz_b = main.BusinessProfile(business_code='BC-B', company_name='Beta Stores', currency='NGN (₦)',
                                          subscription_plan='business')
        self.db.add_all([self.biz_a, self.biz_b]); self.db.commit()
        for biz in (self.biz_a, self.biz_b):
            self.db.add(main.BusinessSubscription(business_id=biz.id, plan='business', billing_interval='monthly',
                                                  status='active', current_period_start=now,
                                                  current_period_end=now + timedelta(days=30), card_verified=True))
        self.db.commit()
        self.admin_a = self.user('alpha_admin', 'admin', self.biz_a.id)
        self.admin_b = self.user('beta_admin', 'admin', self.biz_b.id)
        self.warehouse_a = self.warehouse(self.biz_a.id)
        self.warehouse_b = self.warehouse(self.biz_b.id)
        self.barcoded = self.product('Alpha Rice 50kg', 'A-RICE', EAN_A, self.biz_a.id)
        self.bare = self.product('Alpha Sugar 1kg', 'A-SUG', None, self.biz_a.id)
        self.client = TestClient(main.app)

    def tearDown(self):
        self.client.close(); main.app.dependency_overrides.clear()
        self.db.close(); self.engine.dispose()

    def user(self, username, role, business_id):
        u = main.User(username=username, email=f'{username}@example.com', password='x',
                      phone=f'+2348030{len(username):04d}0', role=role, business_id=business_id)
        self.db.add(u); self.db.commit(); self.db.refresh(u)
        return u

    def warehouse(self, business_id):
        w = main.Warehouse(business_id=business_id, name='Main Central Warehouse')
        self.db.add(w); self.db.commit(); self.db.refresh(w)
        return w

    def product(self, name, sku, barcode, business_id):
        p = main.Product(name=name, sku=sku, barcode=barcode, category='Grains', business_id=business_id,
                         cost_price=1000.0, retail_price=1400.0, quantity=5, min_stock_level=1,
                         created_at=datetime.utcnow())
        self.db.add(p); self.db.commit(); self.db.refresh(p)
        return p

    def auth(self, user):
        return {'Authorization': f'Bearer {main.issue_token(user, self.db)}'}

    def lookup(self, barcode, user=None):
        return self.client.post('/catalog/barcode-lookup', json={'barcode': barcode},
                                headers=self.auth(user or self.admin_a))

    def allowance_used(self, business_id):
        row = self.db.query(main.AuthFailure).filter(
            main.AuthFailure.scope == main.EXTERNAL_LOOKUP_SCOPE,
            main.AuthFailure.key_hash == main.fail_key(main.EXTERNAL_LOOKUP_SCOPE, str(business_id))).first()
        return row.failures if row else 0

    # ============================================================ A. Edit Product barcode
    def test_a_product_without_a_barcode_can_be_given_one(self):
        r = self.client.patch(f'/products/{self.bare.id}', json={'barcode': EAN_B}, headers=self.auth(self.admin_a))
        self.assertEqual(r.status_code, 200, r.text)
        self.db.expire_all()
        self.assertEqual(self.db.get(main.Product, self.bare.id).barcode, EAN_B)

    def test_an_existing_barcode_can_be_replaced(self):
        r = self.client.patch(f'/products/{self.barcoded.id}', json={'barcode': EAN_B}, headers=self.auth(self.admin_a))
        self.assertEqual(r.status_code, 200, r.text)
        self.db.expire_all()
        self.assertEqual(self.db.get(main.Product, self.barcoded.id).barcode, EAN_B)

    def test_a_barcode_is_normalized_on_edit(self):
        r = self.client.patch(f'/products/{self.bare.id}', json={'barcode': ' 4006-381 333931 '},
                              headers=self.auth(self.admin_a))
        self.assertEqual(r.status_code, 200, r.text)
        self.db.expire_all()
        self.assertEqual(self.db.get(main.Product, self.bare.id).barcode, EAN_B)

    def test_clearing_the_field_clears_the_barcode(self):
        r = self.client.patch(f'/products/{self.barcoded.id}', json={'barcode': None}, headers=self.auth(self.admin_a))
        self.assertEqual(r.status_code, 200, r.text)
        self.db.expire_all()
        self.assertIsNone(self.db.get(main.Product, self.barcoded.id).barcode)

    def test_a_barcode_already_used_in_this_business_is_refused_with_a_clear_message(self):
        r = self.client.patch(f'/products/{self.bare.id}', json={'barcode': EAN_A}, headers=self.auth(self.admin_a))
        self.assertEqual(r.status_code, 409, r.text)
        self.assertIn('already used by another product in this business', r.json()['detail'])
        self.db.expire_all()
        self.assertIsNone(self.db.get(main.Product, self.bare.id).barcode, 'the rejected edit must not be applied')

    def test_editing_a_product_never_triggers_an_external_lookup(self):
        with patch('upcitemdb_provider.lookup_upcitemdb_detailed',
                   side_effect=AssertionError('editing a product must never call the provider')):
            r = self.client.patch(f'/products/{self.bare.id}', json={'barcode': EAN_B}, headers=self.auth(self.admin_a))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.allowance_used(self.biz_a.id), 0)

    # ------------------------------------------------ cross-tenant
    def test_two_businesses_may_hold_the_same_barcode(self):
        theirs = self.product('Beta Rice 50kg', 'B-RICE', None, self.biz_b.id)
        r = self.client.patch(f'/products/{theirs.id}', json={'barcode': EAN_A}, headers=self.auth(self.admin_b))
        self.assertEqual(r.status_code, 200, r.text)
        self.db.expire_all()
        self.assertEqual(self.db.get(main.Product, theirs.id).barcode, EAN_A)
        self.assertEqual(self.db.get(main.Product, self.barcoded.id).barcode, EAN_A)

    def test_one_business_cannot_edit_another_businesss_product(self):
        r = self.client.patch(f'/products/{self.barcoded.id}', json={'barcode': EAN_B}, headers=self.auth(self.admin_b))
        self.assertEqual(r.status_code, 404, r.text)
        self.db.expire_all()
        self.assertEqual(self.db.get(main.Product, self.barcoded.id).barcode, EAN_A)

    def test_a_lookup_never_returns_another_businesss_product(self):
        with patch('upcitemdb_provider.lookup_upcitemdb_detailed', return_value=_miss()):
            r = self.lookup(EAN_A, user=self.admin_b)
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertFalse(body.get('duplicate'))
        self.assertNotIn('product_id', body)
        self.assertEqual(body['source'], 'not_found')

    # ============================================================ B. External-lookup allowance
    def test_an_own_inventory_hit_consumes_no_allowance(self):
        with patch('upcitemdb_provider.lookup_upcitemdb_detailed',
                   side_effect=AssertionError('own-inventory hit must not reach the provider')):
            r = self.lookup(EAN_A)
        self.assertEqual(r.json()['source'], 'own_inventory')
        self.assertEqual(self.allowance_used(self.biz_a.id), 0)

    def test_a_general_catalog_hit_consumes_no_allowance(self):
        self.db.add(main.GeneralCatalog(barcode=EAN_C, catalog_key=f'barcode:{EAN_C}',
                                        product_name='Cached Identity', source='upcitemdb'))
        self.db.commit()
        with patch('upcitemdb_provider.lookup_upcitemdb_detailed',
                   side_effect=AssertionError('catalog hit must not reach the provider')):
            r = self.lookup(EAN_C)
        self.assertEqual(r.json()['source'], 'cauldra_catalog')
        self.assertEqual(self.allowance_used(self.biz_a.id), 0)

    def test_a_provider_hit_consumes_allowance(self):
        with patch('upcitemdb_provider.lookup_upcitemdb_detailed', return_value=_hit()):
            r = self.lookup(EAN_C)
        self.assertEqual(r.json()['source'], 'upcitemdb')
        self.assertEqual(self.allowance_used(self.biz_a.id), 1)

    def test_a_provider_miss_also_consumes_allowance(self):
        with patch('upcitemdb_provider.lookup_upcitemdb_detailed', return_value=_miss()):
            r = self.lookup(EAN_C)
        self.assertEqual(r.json()['source'], 'not_found')
        self.assertEqual(self.allowance_used(self.biz_a.id), 1)

    def test_a_spent_allowance_stops_calling_the_provider_and_offers_manual_entry(self):
        with patch.object(main, 'EXTERNAL_LOOKUP_DAILY_LIMIT', 2):
            with patch('upcitemdb_provider.lookup_upcitemdb_detailed', return_value=_miss()) as mocked:
                self.lookup('4006381333900')
                self.lookup('4006381333901')
                self.assertEqual(mocked.call_count, 2)
            with patch('upcitemdb_provider.lookup_upcitemdb_detailed',
                       side_effect=AssertionError('allowance spent — must not call the provider')):
                r = self.lookup('4006381333902')
        body = r.json()
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(body['found'])
        self.assertEqual(body['source'], 'upcitemdb_unavailable')
        self.assertTrue(body['manual_entry'], 'manual entry must remain possible')

    def test_a_spent_allowance_never_blocks_a_local_hit(self):
        with patch.object(main, 'EXTERNAL_LOOKUP_DAILY_LIMIT', 1):
            with patch('upcitemdb_provider.lookup_upcitemdb_detailed', return_value=_miss()):
                self.lookup('4006381333900')
            with patch('upcitemdb_provider.lookup_upcitemdb_detailed',
                       side_effect=AssertionError('must not reach the provider')):
                r = self.lookup(EAN_A)
        self.assertEqual(r.json()['source'], 'own_inventory')

    def test_one_business_cannot_exhaust_another_businesss_allowance(self):
        with patch.object(main, 'EXTERNAL_LOOKUP_DAILY_LIMIT', 1):
            with patch('upcitemdb_provider.lookup_upcitemdb_detailed', return_value=_miss()):
                self.lookup('4006381333900', user=self.admin_a)
                r_a = self.lookup('4006381333901', user=self.admin_a)
                self.assertEqual(r_a.json()['source'], 'upcitemdb_unavailable')
                r_b = self.lookup('4006381333902', user=self.admin_b)
        self.assertEqual(r_b.json()['source'], 'not_found', 'Business B keeps its own allowance')
        self.assertEqual(self.allowance_used(self.biz_a.id), 1)
        self.assertEqual(self.allowance_used(self.biz_b.id), 1)

    def test_the_allowance_window_restarts_once_it_lapses(self):
        with patch.object(main, 'EXTERNAL_LOOKUP_DAILY_LIMIT', 1):
            with patch('upcitemdb_provider.lookup_upcitemdb_detailed', return_value=_miss()):
                self.lookup('4006381333900')
            row = self.db.query(main.AuthFailure).filter(main.AuthFailure.scope == main.EXTERNAL_LOOKUP_SCOPE).first()
            row.window_started_at = datetime.utcnow() - timedelta(seconds=main.EXTERNAL_LOOKUP_WINDOW_SECONDS + 60)
            self.db.commit()
            with patch('upcitemdb_provider.lookup_upcitemdb_detailed', return_value=_miss()) as mocked:
                r = self.lookup('4006381333901')
                self.assertEqual(mocked.call_count, 1)
        self.assertEqual(r.json()['source'], 'not_found')

    def test_the_allowance_uses_no_new_table(self):
        """The safeguard rides the existing auth_failures ledger — no migration."""
        self.assertIn('auth_failures', main.Base.metadata.tables)
        self.assertEqual(main.AuthFailure.__tablename__, 'auth_failures')
        for name in main.Base.metadata.tables:
            self.assertNotIn('barcode', name.lower())
            self.assertNotIn('lookup', name.lower())

    def test_the_default_allowance_is_conservative_against_a_shared_daily_quota(self):
        self.assertGreater(main.EXTERNAL_LOOKUP_DAILY_LIMIT, 0)
        self.assertLessEqual(main.EXTERNAL_LOOKUP_DAILY_LIMIT, 50,
                             'one business must not be able to drain a ~100/day shared pool')
        self.assertEqual(main.EXTERNAL_LOOKUP_WINDOW_SECONDS, 86400)

    # ============================================================ provider outcomes still map correctly
    def test_a_rate_limited_provider_is_reported_as_unavailable_not_as_not_found(self):
        with patch('upcitemdb_provider.lookup_upcitemdb_detailed', return_value=_rate_limited()):
            r = self.lookup(EAN_C)
        body = r.json()
        self.assertEqual(body['source'], 'upcitemdb_unavailable')
        self.assertTrue(body['manual_entry'])

    def test_a_provider_hit_is_cached_and_never_creates_a_product(self):
        before = self.db.query(main.Product).count()
        with patch('upcitemdb_provider.lookup_upcitemdb_detailed', return_value=_hit(name='Freshly Found')):
            r = self.lookup(EAN_C)
        self.assertEqual(r.json()['product_name'], 'Freshly Found')
        self.assertEqual(self.db.query(main.Product).count(), before, 'a lookup must never create a product')
        cached = self.db.query(main.GeneralCatalog).filter(main.GeneralCatalog.barcode == EAN_C).one()
        self.assertEqual(cached.source, 'upcitemdb')

    def test_no_provider_pricing_or_category_is_ever_stored_or_returned(self):
        greedy = _hit()
        greedy['identity'] = dict(greedy['identity'])
        with patch('upcitemdb_provider.lookup_upcitemdb_detailed', return_value=greedy):
            body = self.lookup(EAN_C).json()
        self.assertEqual(set(body), {'found', 'source', 'barcode', 'product_name', 'brand', 'size'})
        cached = self.db.query(main.GeneralCatalog).filter(main.GeneralCatalog.barcode == EAN_C).one()
        # GC-F1: the retired category column is no longer written at all (it
        # used to hold the "General" placeholder) — the provider's is never imported.
        self.assertIsNone(cached.category, 'the provider category is never imported')


if __name__ == '__main__':
    unittest.main()
