"""Batch D — inventory, purchasing, supplier and Price Monitor correctness
(launch triage 6, 41–50).

  DATA-001/002  product create/edit refuse negative values; negative stock is "out"
  PM-001        supplier prices must be > 0; no sign-inverted or zero-base percentage
  PM-002        a source can be removed (deactivated, history kept); one count definition
  PM-003        no duplicate source for the same supplier/product/source relationship
  PROC-001      reorder quantity accounts for stock on hand
  SUP-001       supplier email validated on create, edit and send
  COPY-001      "1 unit", never "1 units", in supplier-facing text
  GC-008        equivalent metric sizes match in the duplicate detector
  GC-F1         the retired catalog category is no longer written
  OCR-UI-001    the invoice picker offers only what the scanner accepts

Disposable SQLite + the Batch C fixtures (Tenant A/B, Admin/Manager/Staff,
plan switching). Plan ids are billing keys: 'core' = Starter (no Price
Monitor), 'starter' = Business, 'business' = Premium.
"""
import os
import tempfile
os.environ['PYTHON_DOTENV_DISABLED'] = '1'
os.environ['SUPPLY_AI_SKIP_DB_STARTUP_CHECK'] = 'true'
os.environ['DATABASE_URL'] = 'postgresql+psycopg://test:test@127.0.0.1:65432/cauldra_test'
os.environ['SUPPLY_AI_SECRET_KEY'] = 'isolated-test-secret-012345678901234567890123456789'
os.environ.setdefault('SUPPLY_AI_UPLOAD_DIR', tempfile.mkdtemp(prefix='cauldra-batchd-'))
import base64
import json
import re
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import main
from tests import test_batch_c_billing_staff as bc

ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / 'frontend' / 'js' / 'app.js').read_text(encoding='utf-8')
INDEX_HTML = (ROOT / 'frontend' / 'index.html').read_text(encoding='utf-8')


class BatchDBase(bc.BatchCBase):
    def setUp(self):
        super().setUp()
        for biz in (self.biz_a, self.biz_b):
            loc = main.Location(business_id=biz.id, name=f'{biz.company_name} Main', is_main=True, is_active=True,
                                currency='NGN')
            self.db.add(loc); self.db.flush()
            self.db.add(main.Warehouse(business_id=biz.id, name='Main Central Warehouse', is_active=True,
                                       location_id=loc.id))
        self.db.commit()

    def warehouse(self, biz):
        return self.db.query(main.Warehouse).filter_by(business_id=biz.id).one()

    def stocked(self, biz, name, qty, min_level, cost=10.0):
        wh = self.warehouse(biz)
        p = main.Product(name=name, sku=re.sub(r'\W', '', name)[:10].upper(), category='Food', quantity=qty,
                         min_stock_level=min_level, cost_price=cost, retail_price=cost * 1.5, business_id=biz.id,
                         warehouse=wh.name, warehouse_id=wh.id)
        self.db.add(p); self.db.flush()
        self.db.add(main.WarehouseStock(business_id=biz.id, product_id=p.id, warehouse=wh.name, warehouse_id=wh.id,
                                        quantity=qty))
        self.db.commit(); self.db.refresh(p)
        return p

    def new_product(self, user, **overrides):
        body = {'name': 'Palm Oil', 'category': 'Food', 'size': '5L', 'quantity': 10, 'min_stock_level': 2,
                'cost_price': 100.0, 'wholesale_price': 110.0, 'retail_price': 130.0,
                'warehouse': 'Main Central Warehouse'}
        body.update(overrides)
        return self.client.post('/products/', headers=self.auth(user), json=body)


# ============================================================ DATA-001 / 002
class ProductValueTests(BatchDBase):
    def test_create_refuses_each_negative_value_with_a_readable_400(self):
        for field, label in main.PRODUCT_AMOUNT_LABELS:
            with self.subTest(field=field):
                r = self.new_product(self.admin, **{field: -1}, name=f'Neg {field}')
                self.assertEqual(r.status_code, 400, r.text)
                self.assertEqual(r.json()['detail'], f'{label} cannot be negative.')
        self.assertEqual(self.db.query(main.Product).count(), 0, 'nothing is saved')

    def test_create_refuses_non_finite_values(self):
        r = self.new_product(self.admin, cost_price='NaN')
        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn('real number', r.json()['detail'])

    def test_zero_and_positive_values_are_accepted(self):
        r = self.new_product(self.admin, quantity=0, min_stock_level=0, cost_price=0, wholesale_price=0, retail_price=0)
        self.assertEqual(r.status_code, 200, r.text)
        r = self.new_product(self.manager, name='Rice 50kg', quantity=1, cost_price=0.01, retail_price=1)
        self.assertEqual(r.status_code, 200, r.text)

    def test_staff_create_still_follows_permissions_before_validation(self):
        self.assertEqual(self.new_product(self.staff, name='Staff item').status_code, 200)
        self.staff.permission_overrides = json.dumps({'inventory.add_product': False}); self.db.commit()
        r = self.new_product(self.staff, name='Denied', quantity=-5)
        self.assertEqual(r.status_code, 403, 'a denied user learns nothing about validation')

    def test_edit_refuses_negative_values_with_the_same_message(self):
        p = self.stocked(self.biz_a, 'Beans', 5, 1)
        for field, label in main.PRODUCT_AMOUNT_LABELS:
            with self.subTest(field=field):
                r = self.client.patch(f'/products/{p.id}', headers=self.auth(self.admin), json={field: -3})
                self.assertEqual(r.status_code, 400, r.text)
                self.assertEqual(r.json()['detail'], f'{label} cannot be negative.')
        ok = self.client.patch(f'/products/{p.id}', headers=self.auth(self.admin), json={'quantity': 0, 'retail_price': 0})
        self.assertEqual(ok.status_code, 200, ok.text)

    def test_stock_decrease_is_still_an_explicit_adjustment_that_cannot_go_below_zero(self):
        p = self.stocked(self.biz_a, 'Salt', 5, 1)
        h = self.auth(self.admin)
        self.assertEqual(self.client.patch(f'/products/{p.id}/stock', headers=h, json={'quantity_change': -3}).status_code, 200)
        r = self.client.patch(f'/products/{p.id}/stock', headers=h, json={'quantity_change': -3})
        self.assertEqual(r.status_code, 400)
        self.assertIn('cannot become negative', r.json()['detail'])

    def test_other_tenant_cannot_edit(self):
        p = self.stocked(self.biz_a, 'Tea', 5, 1)
        self.assertEqual(self.client.patch(f'/products/{p.id}', headers=self.auth(self.admin_b), json={'quantity': 1}).status_code, 404)

    def test_negative_stock_already_stored_counts_as_out_of_stock(self):
        self.stocked(self.biz_a, 'Healthy', 50, 5)
        self.stocked(self.biz_a, 'Low', 3, 5)
        self.stocked(self.biz_a, 'Out', 0, 5)
        self.stocked(self.biz_a, 'Legacy negative', -40, 0)
        s = self.client.get('/products/inventory-summary', headers=self.auth(self.admin)).json()
        self.assertEqual((s['total_products'], s['healthy'], s['low'], s['out']), (4, 1, 1, 2))
        self.assertEqual(s['healthy'] + s['low'] + s['out'], s['total_products'])

    def test_frontend_buckets_and_inputs(self):
        self.assertNotIn('p.quantity === 0', APP_JS)
        self.assertIn('list.filter(p => p.quantity <= 0).length', APP_JS)
        for field in ('p-qty', 'p-min', 'p-cost', 'p-wholesale', 'p-retail', 'edit-p-qty', 'edit-p-min',
                      'edit-p-cost', 'edit-p-wholesale', 'edit-p-retail'):
            tag = re.search(rf'<input[^>]*id="{field}"[^>]*>', INDEX_HTML).group(0)
            self.assertIn('min="0"', tag, field)


# ======================================================= PM-001 / 002 / 003
class PriceMonitorTests(BatchDBase):
    def setUp(self):
        super().setUp()
        self.set_sub(self.biz_a, plan='business')   # Premium: Price Monitor included
        self.product, self.supplier = self.product_and_supplier(self.biz_a)
        self.other_supplier = main.Supplier(name='Kano Traders', phone='+2348030000009', business_id=self.biz_a.id)
        self.db.add(self.other_supplier); self.db.commit(); self.db.refresh(self.other_supplier)
        self.h = self.auth(self.admin)

    def add_source(self, supplier=None, **extra):
        body = {'supplier_id': (supplier or self.supplier).id, 'product_id': self.product.id, 'source_type': 'manual'}
        body.update(extra)
        return self.client.post('/price-monitor/sources', headers=self.h, json=body)

    def monitor(self, user=None):
        r = self.client.get('/price-monitor', headers=self.auth(user) if user else self.h)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def billing_count(self):
        biz = self.db.get(main.BusinessProfile, self.biz_a.id)
        return main.get_current_entitlement_usage(self.db, biz, 'price_monitor')

    def test_supplier_price_must_be_greater_than_zero(self):
        sid = self.add_source().json()['id']
        for price, detail in ((-50, 'Supplier price cannot be negative.'), (0, 'Supplier price must be greater than zero.')):
            r = self.client.post(f'/price-monitor/{sid}/price', headers=self.h, json={'price': price})
            self.assertEqual((r.status_code, r.json()['detail']), (400, detail))
        self.assertEqual(self.db.query(main.PriceHistory).count(), 0)
        self.assertEqual(self.client.post(f'/price-monitor/{sid}/price', headers=self.h, json={'price': 0.01}).status_code, 200)

    def test_starting_price_must_be_greater_than_zero(self):
        for price in (-5, 0):
            r = self.add_source(initial_price=price)
            self.assertEqual(r.status_code, 400, r.text)
        self.assertEqual(self.db.query(main.PriceMonitorSource).count(), 0)
        self.assertEqual(self.add_source(initial_price=10).status_code, 200)

    def test_percentage_never_uses_a_zero_or_negative_base(self):
        self.assertEqual(main.price_change_percent(1000, 1200), 20.0)
        self.assertEqual(main.price_change_percent(1200, 1000), -16.67)
        self.assertIsNone(main.price_change_percent(-50, 100), 'was reported as -300%')
        self.assertIsNone(main.price_change_percent(0, 100))
        self.assertIsNone(main.price_change_percent(100, -5))
        # historical bad rows written before this fix
        sid = self.add_source().json()['id']
        for price in (-50, 100):
            self.db.add(main.PriceHistory(source_id=sid, price=price)); self.db.commit()
        self.assertIsNone(self.monitor()['sources'][0]['change_percent'])

    def test_price_list_rows_with_non_positive_prices_are_not_recorded(self):
        def upload(text):
            return self.client.post('/price-monitor/upload-price-list', headers=self.h, json={
                'supplier_id': self.supplier.id, 'product_id': None, 'file_name': 'p.csv',
                'file_data': 'data:text/csv;base64,' + base64.b64encode(text.encode()).decode()})
        r = upload(f'sku,price\n{self.product.sku},-5\n{self.product.sku},0\n')
        self.assertEqual((r.status_code, r.json()['detail']), (400, 'Prices in a price list must be greater than zero.'))
        self.assertEqual(self.db.query(main.PriceHistory).count(), 0)
        r = upload(f'sku,price\n{self.product.sku},0\n{self.product.sku},250\n')
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()['count'], r.json()['invalid_price_rows']), (1, 1))
        self.assertEqual([h.price for h in self.db.query(main.PriceHistory).all()], [250.0])

    def test_duplicate_source_is_refused(self):
        first = self.add_source()
        self.assertEqual(first.status_code, 200)
        again = self.add_source()
        self.assertEqual((again.status_code, again.json()['detail']), (409, 'This supplier is already monitored for this product.'))
        self.assertEqual(self.add_source(supplier=self.other_supplier).status_code, 200, 'another supplier is a different source')
        self.assertEqual(self.add_source(source_type='website', source_url='https://kano.example/rice').status_code, 200)
        self.assertEqual(self.add_source(source_type='website', source_url='https://KANO.example/rice/').status_code, 409)
        self.assertEqual(self.db.query(main.PriceMonitorSource).count(), 3)

    def test_remove_keeps_history_and_every_count_agrees(self):
        sid = self.add_source().json()['id']
        self.add_source(supplier=self.other_supplier)
        self.client.post(f'/price-monitor/{sid}/price', headers=self.h, json={'price': 100})
        self.client.post(f'/price-monitor/{sid}/price', headers=self.h, json={'price': 110})
        self.assertEqual((self.monitor()['active_count'], self.billing_count()), (2, 2))
        r = self.client.delete(f'/price-monitor/sources/{sid}', headers=self.h)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()['message'], 'Price source removed. Its price history is kept.')
        body = self.monitor()
        self.assertEqual((body['active_count'], body['removed_count'], self.billing_count()), (1, 1, 1))
        self.assertEqual(self.db.query(main.PriceHistory).filter_by(source_id=sid).count(), 2, 'history kept')
        self.assertEqual(self.client.post(f'/price-monitor/{sid}/price', headers=self.h, json={'price': 5}).status_code, 409)

    def test_re_adding_a_removed_source_restores_it_instead_of_duplicating(self):
        sid = self.add_source().json()['id']
        self.client.post(f'/price-monitor/{sid}/price', headers=self.h, json={'price': 100})
        self.client.delete(f'/price-monitor/sources/{sid}', headers=self.h)
        r = self.add_source()
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()['id'], r.json().get('reactivated')), (sid, True))
        self.assertEqual(self.db.query(main.PriceMonitorSource).count(), 1)
        self.assertEqual(self.db.query(main.PriceHistory).filter_by(source_id=sid).count(), 1)

    def test_roles_and_tenants(self):
        sid = self.add_source().json()['id']
        self.assertEqual(self.client.post('/price-monitor/sources', headers=self.auth(self.staff), json={
            'supplier_id': self.supplier.id, 'product_id': self.product.id}).status_code, 403)
        self.assertEqual(self.client.delete(f'/price-monitor/sources/{sid}', headers=self.auth(self.staff)).status_code, 403)
        self.assertEqual(self.client.delete(f'/price-monitor/sources/{sid}', headers=self.auth(self.admin_b)).status_code, 404)
        self.assertEqual(self.client.delete(f'/price-monitor/sources/{sid}').status_code, 401)
        self.assertEqual(self.monitor()['active_count'], 1)
        self.assertEqual(self.client.post(f'/price-monitor/{sid}/price', headers=self.auth(self.manager), json={'price': 5}).status_code, 200)

    def test_starter_reads_but_cannot_add_or_restore(self):
        sid = self.add_source().json()['id']
        self.client.delete(f'/price-monitor/sources/{sid}', headers=self.h)
        self.set_sub(self.biz_a, plan='core')   # Starter
        body = self.monitor()
        self.assertFalse(body['included'])
        self.assertEqual([s['id'] for s in body['sources']], [sid])
        for r in (self.add_source(), self.add_source(supplier=self.other_supplier)):
            self.assertEqual(r.status_code, 403, r.text)
        # a removed source is not re-priced on any plan
        self.assertEqual(self.client.post(f'/price-monitor/{sid}/price', headers=self.h, json={'price': 5}).status_code, 409)
        self.assertEqual(self.db.query(main.PriceMonitorSource).filter_by(is_active=True).count(), 0)

    def test_frontend_lists_active_sources_and_offers_remove(self):
        self.assertIn("allSources.filter(s=>s.is_active!==false)", APP_JS)
        self.assertIn('onclick="removePriceSource(${s.id})"', APP_JS)
        self.assertIn("async function removePriceSource(id)", APP_JS)
        self.assertIn("price<=0){showToast(t(\"priceMonitor.invalidPrice\")", APP_JS)


# ================================================================= PROC-001
class ReorderQuantityTests(BatchDBase):
    def test_formula(self):
        cases = [((40, 0), 80), ((25, 8), 42), ((15, 15), 15), ((15, 0), 30), ((0, -40), 40), ((0, 0), 1), ((5, 12), 1)]
        for (minimum, on_hand), expected in cases:
            with self.subTest(minimum=minimum, on_hand=on_hand):
                self.assertEqual(main.reorder_quantity(minimum, on_hand), expected)

    def test_generated_po_uses_stock_on_hand(self):
        self.stocked(self.biz_a, 'Vegetable Oil', 8, 25, cost=10)
        self.stocked(self.biz_a, 'Bar Soap', 15, 15, cost=2)
        self.stocked(self.biz_a, 'Negative Trace', -40, 0, cost=1)
        self.stocked(self.biz_a, 'Plenty', 100, 5)
        r = self.client.post('/purchase-orders/generate', headers=self.auth(self.admin))
        self.assertEqual(r.status_code, 200, r.text)
        po = self.db.get(main.PurchaseOrder, r.json()['id'])
        self.assertIn('Vegetable Oil (42 units)', po.email_draft)
        self.assertIn('Bar Soap (15 units)', po.email_draft)
        self.assertIn('Negative Trace (40 units)', po.email_draft)
        self.assertNotIn('Plenty', po.email_draft)
        self.assertAlmostEqual(po.total_estimated_cost, 42 * 10 + 15 * 2 + 40 * 1)

    def test_one_unit_is_singular_in_supplier_text(self):
        self.stocked(self.biz_a, 'Single', 0, 0, cost=1)
        r = self.client.post('/purchase-orders/generate', headers=self.auth(self.admin))
        po = self.db.get(main.PurchaseOrder, r.json()['id'])
        self.assertIn('Single (1 unit)', po.email_draft)
        self.assertNotIn('1 units', po.email_draft)

    def test_other_tenant_stock_is_never_included(self):
        self.stocked(self.biz_b, 'B only', 0, 5)
        self.assertEqual(self.client.post('/purchase-orders/generate', headers=self.auth(self.admin)).status_code, 400)


# ================================================================= COPY-001
class PluralisationTests(unittest.TestCase):
    def test_units_label(self):
        self.assertEqual([main.units_label(n) for n in (0, 1, 2, 1.0, -1)], ['0 units', '1 unit', '2 units', '1.0 unit', '-1 units'])

    def test_frontend_instances(self):
        self.assertIn('skuCount: { one: "{count} SKU", other: "{count} SKUs" }', APP_JS)
        self.assertIn("tPlural('warehouses.skuCount', w.sku_count ?? 0)", APP_JS)
        self.assertIn('cartUnits === 1 ? "item" : "items"', APP_JS)
        self.assertNotRegex(APP_JS, r"\} items`;")
        # a locale that keeps one plain string is used as-is, never replaced by English
        self.assertIn('(typeof plain === "string" ? plain : undefined)', APP_JS)


# ================================================================== SUP-001
class SupplierEmailTests(BatchDBase):
    def create(self, email, user=None):
        return self.client.post('/suppliers/', headers=self.auth(user or self.admin),
                                json={'name': 'Kano Traders', 'phone': '+2348030000009', 'contact_email': email})

    def test_create_validates_on_the_server(self):
        for bad in ('not-an-email', 'a@b', 'x@@y.com', 'spaces in@x.com', '@example.com'):
            with self.subTest(bad=bad):
                r = self.create(bad)
                self.assertEqual((r.status_code, r.json()['detail']), (400, 'Enter a valid supplier email address, or leave it blank.'))
        self.assertEqual(self.db.query(main.Supplier).count(), 0)
        self.assertEqual(self.create('').status_code, 200)
        r = self.create('Orders@Kano-Traders.EXAMPLE.com')
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.db.get(main.Supplier, r.json()['id']).contact_email, 'Orders@kano-traders.example.com')

    def test_edit_uses_the_same_rule(self):
        sid = self.create('ok@example.com').json()['id']
        h = self.auth(self.admin)
        self.assertEqual(self.client.patch(f'/suppliers/{sid}', headers=h, json={'contact_email': 'a@b'}).status_code, 400)
        self.assertEqual(self.client.patch(f'/suppliers/{sid}', headers=h, json={'contact_email': ''}).status_code, 200)
        self.assertEqual(self.client.patch(f'/suppliers/{sid}', headers=self.auth(self.admin_b), json={'contact_email': 'x@example.com'}).status_code, 404)

    def test_sending_to_a_stored_invalid_address_is_a_clear_400_not_try_later(self):
        s = main.Supplier(name='Legacy', phone='+234803', contact_email='also-not-an-email', business_id=self.biz_a.id)
        self.db.add(s); self.db.flush()
        loc = self.db.query(main.Location).filter_by(business_id=self.biz_a.id).one()
        po = main.PurchaseOrder(status='DRAFT', total_estimated_cost=1, email_draft='x', business_id=self.biz_a.id,
                                location_id=loc.id, currency_snapshot='NGN', supplier_id=s.id)
        self.db.add(po); self.db.commit()
        r = self.client.post(f'/purchase-orders/{po.id}/dispatch-email', headers=self.auth(self.admin))
        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn('not valid', r.json()['detail'])


# ================================================================== GC-008
class SizeNormalisationTests(BatchDBase):
    def test_equivalent_metric_sizes_compare_equal(self):
        same = [('50cl', '500ml'), ('0.5L', '500 ml'), ('1L', '1000ml'), ('1 litre', '100cl'), ('1kg', '1000g'),
                ('0,5l', '500ml'), ('2 Litres', '2000ML')]
        for a, b in same:
            with self.subTest(a=a, b=b):
                self.assertEqual(main._dup_norm_size(a), main._dup_norm_size(b))
        different = [('500ml', '500g'), ('50cl', '5l'), ('1kg', '100g'), ('medium', 'large'), ('medium', '500ml')]
        for a, b in different:
            with self.subTest(a=a, b=b):
                self.assertNotEqual(main._dup_norm_size(a), main._dup_norm_size(b))
        self.assertEqual(main._dup_norm_size('Family'), 'family', 'a vague word is compared as text, never converted')

    def test_duplicate_detector_scores_50cl_and_500ml_as_the_same_product(self):
        r = self.new_product(self.admin, name='GC Oil', size='50cl', quantity=5)
        self.assertEqual(r.status_code, 200, r.text)
        dup = self.new_product(self.admin, name='GC Oil', size='500ml', quantity=5)
        self.assertEqual(dup.status_code, 409, dup.text)
        body = dup.json()['detail']
        signals = json.dumps(body)
        self.assertIn('same_size', signals)
        self.assertIn('definite', signals)

    def test_original_size_text_is_kept(self):
        r = self.new_product(self.admin, name='GC Juice', size='50cl')
        self.assertEqual(self.db.get(main.Product, r.json()['id']).size, '50cl')


# =================================================================== GC-F1
class RetiredCatalogCategoryTests(BatchDBase):
    def test_new_catalog_rows_carry_no_category(self):
        self.assertTrue(main.GeneralCatalog.__table__.c.category.nullable)
        self.assertIsNone(main.GeneralCatalog.__table__.c.category.default)
        source = (ROOT / 'backend' / 'main.py').read_text(encoding='utf-8')
        self.assertNotIn('category="General"', source)

    def test_migration_only_relaxes_the_constraint(self):
        text = (ROOT / 'alembic' / 'versions' / '0041_general_catalog_category_retired.py').read_text(encoding='utf-8')
        self.assertIn('down_revision = "0040_price_monitor_source_lifecycle"', text)
        self.assertIn('nullable=True', text)
        self.assertNotIn('drop_column', text)
        self.assertNotRegex(text.split('def downgrade')[0], r'UPDATE|DELETE')


# ============================================================== OCR-UI-001
class InvoicePickerTests(unittest.TestCase):
    def test_picker_offers_only_what_the_scanner_accepts(self):
        tag = re.search(r'<input[^>]*id="global-upload-invoice-input"[^>]*>', INDEX_HTML).group(0)
        accept = re.search(r'accept="([^"]*)"', tag).group(1).split(',')
        self.assertEqual(set(a for a in accept if a.startswith('image/')), {'image/jpeg', 'image/png', 'image/webp'})
        self.assertFalse(any(x in accept for x in ('.pdf', '.doc', '.docx', 'image/*')))
        self.assertIn('const INVOICE_SCAN_TYPES = ["image/jpeg", "image/png", "image/webp"]', APP_JS)
        source = (ROOT / 'backend' / 'main.py').read_text(encoding='utf-8')
        self.assertIn('decode_base64_upload(req.image_data, {"image/jpeg", "image/png", "image/webp"})', source)
        self.assertIn('JPEG, PNG or WebP', INDEX_HTML)


if __name__ == '__main__':
    unittest.main()
