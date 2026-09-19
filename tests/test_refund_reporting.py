"""REFUND-003 (export half) — a refunded sale must not export at full value.

Disposable SQLite, no network: seeds one business day, two sales and a refund
against one of them, then reads the shared export row builder.
"""
import os
os.environ['PYTHON_DOTENV_DISABLED'] = '1'
os.environ['SUPPLY_AI_SKIP_DB_STARTUP_CHECK'] = 'true'
os.environ['DATABASE_URL'] = 'postgresql+psycopg://test:test@127.0.0.1:65432/cauldra_test'
os.environ['SUPPLY_AI_SECRET_KEY'] = 'isolated-test-secret-012345678901234567890123456789'
import unittest
from datetime import datetime, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
import main


class SalesExportRefundTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        main.Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()
        self.business = main.BusinessProfile(business_code='EX-1', company_name='Exporter', currency='NGN (₦)')
        self.db.add(self.business); self.db.commit()
        self.user = main.User(username='owner', email='owner@example.com', password='x', phone='+2348030000000', role='admin',
                              business_id=self.business.id)
        self.db.add(self.user); self.db.commit()
        opened = datetime.utcnow() - timedelta(days=2)
        self.day = main.BusinessDay(business_id=self.business.id, date=opened.date(), opened_at=opened,
                                    is_open=False, status='CLOSED', reopen_count=0)
        self.db.add(self.day); self.db.commit()
        self.product = main.Product(name='Rice 50kg', sku='RICE-50', category='Grains',
                                    business_id=self.business.id, cost_price=30000.0, retail_price=46000.0,
                                    quantity=10, min_stock_level=1, created_at=datetime.utcnow())
        self.db.add(self.product); self.db.commit()
        self.sold = main.SaleModel(product_id=self.product.id, business_id=self.business.id, quantity=1,
                                   total_price=46000.0, timestamp=opened, business_day_id=self.day.id,
                                   currency_snapshot='NGN (₦)')
        self.kept = main.SaleModel(product_id=self.product.id, business_id=self.business.id, quantity=2,
                                   total_price=92000.0, timestamp=opened, business_day_id=self.day.id,
                                   currency_snapshot='NGN (₦)')
        self.db.add_all([self.sold, self.kept]); self.db.commit()

    def tearDown(self):
        self.db.close(); self.engine.dispose()

    def refund(self, sale, quantity, amount, when=None):
        """A refund performed LATER than the sale, as a real one usually is."""
        txn = main.RefundTransaction(business_id=self.business.id, business_day_id=self.day.id,
                                     refund_total=amount, created_at=when or datetime.utcnow())
        self.db.add(txn); self.db.commit()
        self.db.add(main.RefundLine(refund_transaction_id=txn.id, business_id=self.business.id,
                                    business_day_id=self.day.id, original_sale_id=sale.id,
                                    product_id=self.product.id, product_name_snapshot=self.product.name,
                                    quantity=quantity, unit_price=amount / quantity, refund_amount=amount,
                                    restocked=True, created_at=datetime.utcnow()))
        self.db.commit()

    def rows(self):
        return main._sales_export_rows(self.db, self.user, self.business, 'all', None, None)

    def columns(self):
        return [c['label'] for c in main.SALES_EXPORT_COLUMNS]

    def test_export_has_refund_columns(self):
        for label in ('REFUNDED QUANTITY', 'REFUND AMOUNT', 'NET TOTAL'):
            self.assertIn(label, self.columns())
        self.assertEqual(len(self.rows()[0]), len(self.columns()), 'every row must match the header width')

    def test_unrefunded_sale_is_unchanged_and_nets_to_its_total(self):
        row = dict(zip(self.columns(), self.rows()[0]))
        self.assertEqual(row['TOTAL'], 46000.0)
        self.assertEqual(row['REFUNDED QUANTITY'], 0)
        self.assertEqual(row['REFUND AMOUNT'], 0)
        self.assertEqual(row['NET TOTAL'], 46000.0)

    def test_fully_refunded_sale_exports_its_refund_and_a_zero_net(self):
        self.refund(self.sold, 1, 46000.0)
        by_id = {r[0]: dict(zip(self.columns(), r)) for r in self.rows()}
        refunded, untouched = by_id[self.sold.id], by_id[self.kept.id]
        self.assertEqual(refunded['TOTAL'], 46000.0, 'the original sale is never rewritten')
        self.assertEqual(refunded['REFUNDED QUANTITY'], 1)
        self.assertEqual(refunded['REFUND AMOUNT'], 46000.0)
        self.assertEqual(refunded['NET TOTAL'], 0.0)
        self.assertEqual((untouched['REFUND AMOUNT'], untouched['NET TOTAL']), (0, 92000.0))

    def test_partial_and_repeated_refunds_accumulate(self):
        self.refund(self.kept, 1, 46000.0)
        self.refund(self.kept, 1, 46000.0)
        row = {r[0]: dict(zip(self.columns(), r)) for r in self.rows()}[self.kept.id]
        self.assertEqual(row['REFUNDED QUANTITY'], 2)
        self.assertEqual(row['REFUND AMOUNT'], 92000.0)
        self.assertEqual(row['NET TOTAL'], 0.0)

    def test_another_business_refund_never_leaks_into_this_export(self):
        other = main.BusinessProfile(business_code='EX-2', company_name='Other', currency='NGN (₦)')
        self.db.add(other); self.db.commit()
        txn = main.RefundTransaction(business_id=other.id, business_day_id=self.day.id,
                                     refund_total=46000.0, created_at=datetime.utcnow())
        self.db.add(txn); self.db.commit()
        self.db.add(main.RefundLine(refund_transaction_id=txn.id, business_id=other.id, business_day_id=self.day.id,
                                    original_sale_id=self.sold.id, product_id=self.product.id,
                                    product_name_snapshot=self.product.name, quantity=1, unit_price=46000.0,
                                    refund_amount=46000.0, restocked=True, created_at=datetime.utcnow()))
        self.db.commit()
        row = {r[0]: dict(zip(self.columns(), r)) for r in self.rows()}[self.sold.id]
        self.assertEqual((row['REFUND AMOUNT'], row['NET TOTAL']), (0, 46000.0))


if __name__ == '__main__':
    unittest.main(verbosity=2)
