"""PM-004 — a supplier price list must upload, and a real barcode must not
abort the request.

The parser coerced the first CSV column to int and compared it against
products.id, so a 12/13-digit barcode reached PostgreSQL as an out-of-range
integer and the whole request died with an unhandled 500. SQLite does not
enforce that range, so these tests pin the resolver's contract (what an
identifier is allowed to resolve to) and the end-to-end upload; the crash itself
is verified against QA's PostgreSQL.
"""
import os
import tempfile
os.environ['PYTHON_DOTENV_DISABLED'] = '1'
os.environ['SUPPLY_AI_SKIP_DB_STARTUP_CHECK'] = 'true'
os.environ['DATABASE_URL'] = 'postgresql+psycopg://test:test@127.0.0.1:65432/cauldra_test'
os.environ['SUPPLY_AI_SECRET_KEY'] = 'isolated-test-secret-012345678901234567890123456789'
os.environ.setdefault('SUPPLY_AI_UPLOAD_DIR', tempfile.mkdtemp(prefix='cauldra-pricelist-'))
import base64
import unittest
from datetime import datetime, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient
import main

EAN = '5901234123457'          # a real 13-digit barcode: 5.9e12 > the id column's 2^31-1
OUT_OF_RANGE = '2147483648'    # one past the id column's maximum


def csv_url(body: str) -> str:
    return 'data:text/csv;base64,' + base64.b64encode(body.encode('utf-8')).decode()


class PriceListUploadTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        main.Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

        def db_dep():
            with self.Session() as session:
                yield session
        main.app.dependency_overrides[main.get_db] = db_dep
        self.db = self.Session()
        self.business = main.BusinessProfile(business_code='PL-1', company_name='Price Lister', currency='NGN (₦)',
                                             subscription_plan='business')
        self.other = main.BusinessProfile(business_code='PL-2', company_name='Neighbour', currency='NGN (₦)',
                                          subscription_plan='business')
        self.db.add_all([self.business, self.other]); self.db.commit()
        now = datetime.utcnow()
        for biz in (self.business, self.other):
            self.db.add(main.BusinessSubscription(business_id=biz.id, plan='business', billing_interval='monthly',
                                                  status='active', current_period_start=now,
                                                  current_period_end=now + timedelta(days=30), card_verified=True))
        self.db.commit()
        self.admin = self.user('owner', 'admin', self.business.id)
        self.staff = self.user('shopkeeper', 'staff', self.business.id)
        self.other_admin = self.user('neighbour', 'admin', self.other.id)
        self.supplier = main.Supplier(name='Kano Traders', phone='+2348031111111', business_id=self.business.id)
        self.other_supplier = main.Supplier(name='Someone Else', phone='+2348032222222', business_id=self.other.id)
        self.db.add_all([self.supplier, self.other_supplier]); self.db.commit()
        self.rice = self.product('Premium Rice 50kg', 'RICE-50', EAN, self.business.id)
        self.sugar = self.product('Sugar, 1kg', 'SUG-1', '5901234123464', self.business.id)
        self.their_rice = self.product('Premium Rice 50kg', 'RICE-50', EAN, self.other.id)
        self.client = TestClient(main.app)

    def tearDown(self):
        self.client.close(); main.app.dependency_overrides.clear()
        self.db.close(); self.engine.dispose()

    def user(self, username, role, business_id):
        u = main.User(username=username, email=f'{username}@example.com', password='x',
                      phone=f'+23480300000{len(username)}', role=role, business_id=business_id)
        self.db.add(u); self.db.commit(); self.db.refresh(u)
        return u

    def product(self, name, sku, barcode, business_id):
        p = main.Product(name=name, sku=sku, barcode=barcode, category='Grains', business_id=business_id,
                         cost_price=38000.0, retail_price=46000.0, quantity=5, min_stock_level=1,
                         created_at=datetime.utcnow())
        self.db.add(p); self.db.commit(); self.db.refresh(p)
        return p

    def auth(self, user):
        return {'Authorization': f'Bearer {main.issue_token(user, self.db)}'}

    def upload(self, body, user=None, product_id=None, file_name='prices.csv', supplier_id=None, mime='text/csv'):
        payload = {'supplier_id': supplier_id if supplier_id is not None else self.supplier.id,
                   'product_id': product_id, 'file_name': file_name,
                   'file_data': ('data:%s;base64,' % mime) + base64.b64encode(body.encode('utf-8')).decode()}
        return self.client.post('/price-monitor/upload-price-list', json=payload,
                                headers=self.auth(user or self.admin))

    # ---------------------------------------------------------------- the crash
    def test_a_real_13_digit_barcode_matches_its_product(self):
        r = self.upload(f'barcode,price\n{EAN},41000\n')
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()['count'], 1)
        source = self.db.query(main.PriceMonitorSource).filter_by(business_id=self.business.id).one()
        self.assertEqual(source.product_id, self.rice.id)
        self.assertEqual(source.last_price, 41000.0)

    def test_a_number_too_large_to_be_a_product_id_is_never_compared_to_one(self):
        """The exact PostgreSQL abort: products.id = 2147483648 -> integer out of range."""
        self.assertIsNone(main.resolve_price_list_product(self.db, self.business.id, OUT_OF_RANGE))
        self.assertIsNone(main.resolve_price_list_product(self.db, self.business.id, EAN.rjust(18, '9')))
        r = self.upload(f'barcode,price\n{OUT_OF_RANGE},41000\n')
        self.assertEqual(r.status_code, 422)
        self.assertIn('matched a product', r.json()['detail'])

    def test_the_id_column_boundary_is_respected(self):
        self.assertIsNone(main.resolve_price_list_product(self.db, self.business.id, str(main.PRODUCT_ID_MAX)))
        self.assertEqual(main.resolve_price_list_product(self.db, self.business.id, str(self.rice.id)), self.rice.id)

    # ---------------------------------------------------------------- identifiers
    def test_sku_barcode_name_and_id_all_resolve(self):
        for identifier, expected in ((self.rice.sku, self.rice.id), (EAN, self.rice.id),
                                     ('Premium Rice 50kg', self.rice.id), (str(self.rice.id), self.rice.id),
                                     ('rice-50', self.rice.id), ('  RICE-50  ', self.rice.id)):
            self.assertEqual(main.resolve_price_list_product(self.db, self.business.id, identifier), expected, identifier)

    def test_an_unknown_identifier_resolves_to_nothing(self):
        for identifier in ('', '   ', 'NOT-A-PRODUCT', '999999', '0'):
            self.assertIsNone(main.resolve_price_list_product(self.db, self.business.id, identifier), identifier)

    def test_an_identifier_matching_two_products_is_refused_not_guessed(self):
        twin = self.product('Premium Rice 50kg', 'RICE-50-B', None, self.business.id)
        self.assertIsNone(main.resolve_price_list_product(self.db, self.business.id, 'Premium Rice 50kg'))
        self.assertEqual(main.resolve_price_list_product(self.db, self.business.id, 'RICE-50-B'), twin.id)

    def test_another_businesss_product_is_never_matched(self):
        self.assertEqual(main.resolve_price_list_product(self.db, self.other.id, EAN), self.their_rice.id)
        r = self.upload(f'barcode,price\n{EAN},41000\n', user=self.other_admin, supplier_id=self.other_supplier.id)
        self.assertEqual(r.status_code, 200, r.text)
        source = self.db.query(main.PriceMonitorSource).filter_by(business_id=self.other.id).one()
        self.assertEqual(source.product_id, self.their_rice.id)
        self.assertEqual(self.db.query(main.PriceMonitorSource).filter_by(business_id=self.business.id).count(), 0)

    # ---------------------------------------------------------------- CSV shapes
    def test_a_header_row_is_ignored_and_a_headerless_first_row_is_kept(self):
        r = self.upload(f'{EAN},41000\n')  # no header at all: the row used to be eaten
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()['count'], 1)

    def test_a_quoted_product_name_containing_a_comma_is_one_field(self):
        r = self.upload('product,price\n"Sugar, 1kg",1450\n')
        self.assertEqual(r.status_code, 200, r.text)
        source = self.db.query(main.PriceMonitorSource).filter_by(product_id=self.sugar.id).one()
        self.assertEqual(source.last_price, 1450.0)

    def test_several_rows_are_all_recorded_and_unmatched_rows_are_reported(self):
        r = self.upload(f'barcode,price\n{EAN},41000\nNOT-A-PRODUCT,500\n{self.sugar.sku},1450\n')
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()['count'], 2)
        self.assertEqual(r.json()['unmatched_rows'], 1)

    def test_a_thousands_separated_price_is_read_in_full(self):
        r = self.upload(f'barcode,price\n{EAN},"41,500.50"\n')
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.db.query(main.PriceMonitorSource).one().last_price, 41500.5)

    def test_an_explicit_product_id_still_overrides_the_first_column(self):
        r = self.upload('anything,price\nignored,39000\n', product_id=self.sugar.id)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.db.query(main.PriceMonitorSource).one().product_id, self.sugar.id)

    def test_a_product_id_belonging_to_another_business_is_refused(self):
        r = self.upload(f'barcode,price\n{EAN},41000\n', product_id=self.their_rice.id)
        self.assertEqual(r.status_code, 404)

    def test_a_non_csv_file_is_still_refused_with_a_clear_message(self):
        r = self.upload('ignored', file_name='pricelist.png', mime='image/png')
        self.assertEqual(r.status_code, 422)
        self.assertIn('CSV', r.json()['detail'])

    def test_a_csv_with_no_usable_row_explains_what_is_expected(self):
        r = self.upload('barcode,price\nNOT-A-PRODUCT,500\n')
        self.assertEqual(r.status_code, 422)
        self.assertIn('SKU, barcode or name', r.json()['detail'])

    # ---------------------------------------------------------------- unchanged rules
    def test_price_history_and_the_upload_record_are_written(self):
        r = self.upload(f'barcode,price\n{EAN},41000\n')
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.db.query(main.PriceHistory).count(), 1)
        self.assertEqual(self.db.query(main.StoredUpload).filter_by(kind='price_list').count(), 1)
        self.assertTrue(self.db.query(main.AuditLog).filter_by(action='PRICE_LIST_UPLOADED').count())

    def test_reuploading_updates_the_same_source(self):
        self.upload(f'barcode,price\n{EAN},41000\n')
        r = self.upload(f'barcode,price\n{EAN},42500\n')
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.db.query(main.PriceMonitorSource).count(), 1)
        self.assertEqual(self.db.query(main.PriceMonitorSource).one().last_price, 42500.0)
        self.assertEqual(self.db.query(main.PriceHistory).count(), 2)

    def test_staff_without_the_permission_cannot_upload(self):
        r = self.upload(f'barcode,price\n{EAN},41000\n', user=self.staff)
        self.assertEqual(r.status_code, 403)

    def test_an_unknown_supplier_is_refused(self):
        r = self.upload(f'barcode,price\n{EAN},41000\n', supplier_id=self.other_supplier.id)
        self.assertEqual(r.status_code, 404)

    def test_the_plan_capacity_limit_still_applies(self):
        limit = main.get_plan_limit(self.db, self.business, 'price_monitor')
        self.assertIsNotNone(limit)
        for i in range(limit):
            p = self.product(f'Filler {i}', f'FILL-{i}', None, self.business.id)
            self.db.add(main.PriceMonitorSource(business_id=self.business.id, supplier_id=self.supplier.id,
                                                product_id=p.id, source_type='price_list', is_active=True))
        self.db.commit()
        r = self.upload(f'barcode,price\n{EAN},41000\n')
        self.assertIn(r.status_code, (402, 409))


if __name__ == '__main__':
    unittest.main()
