"""Batch E — AI, forecasting and Business Brain (launch triage 7, 51–60).

  BRAIN-001  7-day forecasts used a per-business-day rate times 7 CALENDAR days
             (+16.7% for a six-day week); now times the business days the week holds
  UX-008     forecasts are told in whole units; precision is kept internally
  UX-009     confidence rests on history AND measured accuracy, not history alone
  BRAIN-003  an empty "Cauldra Recommends" says why, never "nothing is needed"
  UX-010/011 plain labels, no bare zero counter; invalid or trivial money inputs
             never become the top recommendation
  AI-001     AI Markdown is rendered safely (Node test: test_ai_markdown_render.cjs)
  AI-002     money reaches the model already written in the business's currency
  AI-003     the assistant is given scoped sales figures (Sales Reports permission)
  UX-012     customer-facing AI wording
  UX-013     Generate Insights is a real button (static checks here, Node test too)
  UX-002     one name for Inventory Financial Analysis
  X6         Business Brain really enforces the ai.use baseline its comment claims

Every expected forecast below is worked out by hand in its docstring.
Disposable SQLite + the Batch C fixtures. Plan ids are billing keys:
'core' = Starter, 'starter' = Business, 'business' = Premium.
"""
import os
import tempfile
os.environ['PYTHON_DOTENV_DISABLED'] = '1'
os.environ['SUPPLY_AI_SKIP_DB_STARTUP_CHECK'] = 'true'
os.environ['DATABASE_URL'] = 'postgresql+psycopg://test:test@127.0.0.1:65432/cauldra_test'
os.environ['SUPPLY_AI_SECRET_KEY'] = 'isolated-test-secret-012345678901234567890123456789'
os.environ.setdefault('SUPPLY_AI_UPLOAD_DIR', tempfile.mkdtemp(prefix='cauldra-batche-'))
import json
import re
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

import main
from tests import test_batch_c_billing_staff as bc

ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / 'frontend' / 'js' / 'app.js').read_text(encoding='utf-8')
INDEX_HTML = (ROOT / 'frontend' / 'index.html').read_text(encoding='utf-8')
BASE_CSS = (ROOT / 'frontend' / 'css' / 'base.css').read_text(encoding='utf-8')

# A Monday well in the past, so no seeded day is "today".
MONDAY = date(2026, 6, 1)
assert MONDAY.weekday() == 0


def six_day_weeks(weeks, start=MONDAY):
    """Mon–Sat trading dates for `weeks` whole weeks (Sundays closed)."""
    return [start + timedelta(days=w * 7 + d) for w in range(weeks) for d in range(6)]


class BatchEBase(bc.BatchCBase):
    def setUp(self):
        super().setUp()
        self.loc = {}
        for biz in (self.biz_a, self.biz_b):
            loc = main.Location(business_id=biz.id, name=f'{biz.company_name} Main', is_main=True, is_active=True,
                                currency='NGN')
            self.db.add(loc); self.db.flush()
            self.db.add(main.Warehouse(business_id=biz.id, name='Main Central Warehouse', is_active=True,
                                       location_id=loc.id))
            self.loc[biz.id] = loc
        self.db.commit()

    def warehouse(self, biz):
        return self.db.query(main.Warehouse).filter_by(business_id=biz.id).one()

    def product(self, biz, name, qty, retail=1000.0, cost=800.0, wholesale=900.0, min_level=0):
        wh = self.warehouse(biz)
        p = main.Product(name=name, sku=re.sub(r'\W', '', name)[:10].upper(), category='Food', quantity=qty,
                         min_stock_level=min_level, cost_price=cost, wholesale_price=wholesale, retail_price=retail,
                         business_id=biz.id, warehouse=wh.name, warehouse_id=wh.id)
        self.db.add(p); self.db.flush()
        self.db.add(main.WarehouseStock(business_id=biz.id, product_id=p.id, warehouse=wh.name,
                                        warehouse_id=wh.id, quantity=qty))
        self.db.commit(); self.db.refresh(p)
        return p

    def close_days(self, biz, dates, sales=None, is_open=False):
        """One Business Day per date; `sales` maps product -> units sold that day."""
        days = []
        for d in dates:
            opened = datetime(d.year, d.month, d.day, 8, 0)
            day = main.BusinessDay(business_id=biz.id, location_id=self.loc[biz.id].id, date=d.isoformat(),
                                   opened_at=opened, closed_at=None if is_open else opened + timedelta(hours=10),
                                   is_open=is_open, status='OPEN' if is_open else 'CLOSED')
            self.db.add(day); self.db.flush()
            for product, units in (sales or {}).items():
                if units:
                    self.sale(biz, product, units, opened + timedelta(hours=2), day)
            days.append(day)
        self.db.commit()
        return days

    def sale(self, biz, product, units, when, day=None, unit_price=None):
        price = product.retail_price if unit_price is None else unit_price
        self.db.add(main.SaleModel(business_id=biz.id, product_id=product.id, quantity=units,
                                   total_price=units * price, unit_price=price, timestamp=when,
                                   business_day_id=day.id if day else None, currency_snapshot='NGN',
                                   product_name_snapshot=product.name, client_ref=f'c-{product.id}-{when.isoformat()}'))

    def brain(self, user):
        r = self.client.get('/business-brain', headers=self.auth(user))
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()


# ============================================================ BRAIN-001 (#7)
class TradingWeekTests(unittest.TestCase):
    def test_six_day_trading_weeks_hold_exactly_six_business_days(self):
        self.assertEqual(main.trading_days_per_week(six_day_weeks(4)), 6.0)

    def test_the_audit_span_27_trading_days_over_31_calendar_days_is_six(self):
        """The audit's fixture: 31 consecutive dates with the 4 Sundays closed."""
        start = date(2026, 3, 2)  # Monday
        dates = [start + timedelta(days=i) for i in range(31) if (start + timedelta(days=i)).weekday() != 6]
        self.assertEqual(len(dates), 27)
        self.assertEqual(main.trading_days_per_week(dates), 6.0)

    def test_seven_and_five_day_businesses(self):
        every_day = [MONDAY + timedelta(days=i) for i in range(21)]
        weekdays_only = [d for d in every_day if d.weekday() < 5]
        self.assertEqual(main.trading_days_per_week(every_day), 7.0)
        self.assertEqual(main.trading_days_per_week(weekdays_only), 5.0)

    def test_no_history_and_short_history_do_not_invent_closures(self):
        self.assertEqual(main.trading_days_per_week([]), 7.0)
        # Mon–Wed only: Thu–Sun were never inside the span, so no evidence they close.
        self.assertEqual(main.trading_days_per_week([MONDAY, MONDAY + timedelta(1), MONDAY + timedelta(2)]), 7.0)

    def test_horizon_conversion_is_not_a_correction_factor(self):
        # 6 units per business day, 6-day week: 6 × 6 = 36 in 7 calendar days (the old code: 42).
        self.assertAlmostEqual(main.forecast_units_for_horizon(6.0, 6.0, 7), 36.0)
        # A seven-day business is unchanged: 6 × 7 = 42.
        self.assertAlmostEqual(main.forecast_units_for_horizon(6.0, 7.0, 7), 42.0)


class ForecastEndToEndTests(BatchEBase):
    def seed_six_day_business(self):
        """4 weeks, Mon–Sat = 24 closed business days; Rice sells 6 units each day
        (144 units). Per business day 144 / 24 = 6. Seven calendar days hold 6
        business days, so the 7-day forecast is 6 × 6 = 36 units (old: 6 × 7 = 42).
        Stock 30: calendar rate 36 / 7 = 5.142857/day -> 30 / 5.142857 = 5.8 days
        of stock (old: 30 / 6 = 5.0)."""
        rice = self.product(self.biz_a, 'Rice 50kg', qty=30, retail=46000.0, cost=40000.0, wholesale=43000.0)
        self.close_days(self.biz_a, six_day_weeks(4), {rice: 6})
        return rice

    def test_business_brain_forecast_is_36_not_42(self):
        self.seed_six_day_business()
        data = self.brain(self.admin)
        coming = [c for c in data['coming'] if c['kind'] == 'velocity']
        self.assertEqual(len(coming), 1)
        self.assertAlmostEqual(coming[0]['predicted_units'], 36.0)
        self.assertEqual(coming[0]['evidence']['business_days_per_week'], 6.0)
        self.assertAlmostEqual(coming[0]['evidence']['raw_expected_units'], 36.0)

    def test_audit_replica_184_units_over_27_business_days_forecasts_40_89(self):
        """184 units over 27 business days (Sundays closed, 31-day span):
        184 / 27 = 6.8148 per business day × 6 business days = 40.89 (old: 47.70)."""
        oil = self.product(self.biz_a, 'Vegetable Oil 5L', qty=0, retail=35000.0)
        start = date(2026, 3, 2)
        dates = [start + timedelta(days=i) for i in range(31) if (start + timedelta(days=i)).weekday() != 6]
        per_day = [7] * 22 + [6] * 5  # 154 + 30 = 184 units
        for d, units in zip(dates, per_day):
            self.close_days(self.biz_a, [d], {oil: units})
        coming = [c for c in self.brain(self.admin)['coming'] if c['kind'] == 'velocity']
        self.assertAlmostEqual(coming[0]['predicted_units'], 40.89, places=2)
        rec = self.db.query(main.BusinessBrainRecommendation).filter_by(business_id=self.biz_a.id, kind='forecast_stockout').one()
        self.assertIn('about 41 units', rec.summary)
        self.assertNotIn('40.89', rec.summary)

    def test_predictive_forecast_days_of_stock_are_calendar_days(self):
        self.seed_six_day_business()
        r = self.client.get('/products/predictive-forecast', headers=self.auth(self.admin))
        self.assertEqual(r.status_code, 200, r.text)
        row = r.json()['forecast'][0]
        self.assertEqual(r.json()['trading_days_per_week'], 6.0)
        self.assertEqual(row['daily_velocity'], 6.0)           # per business day, unchanged meaning
        self.assertEqual(row['days_to_stockout'], 5.8)          # 30 / (36 / 7)

    def test_financial_analysis_uses_the_same_7_day_demand(self):
        """Money at risk = (36 expected − 30 in stock) × ₦46,000 = 6 × 46,000 = ₦276,000
        (old: (42 − 30) × 46,000 = ₦552,000)."""
        self.seed_six_day_business()
        r = self.client.get('/products/financial-intelligence', headers=self.auth(self.admin))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertAlmostEqual(r.json()['money_at_risk']['total'], 276000.0)

    def test_sales_in_the_open_business_day_do_not_inflate_the_rate(self):
        """The open day is not in the 24-day denominator, so its 50 units stay out
        of the numerator too: the forecast stays 36."""
        rice = self.seed_six_day_business()
        self.close_days(self.biz_a, [MONDAY + timedelta(days=28)], {rice: 50}, is_open=True)
        coming = [c for c in self.brain(self.admin)['coming'] if c['kind'] == 'velocity']
        self.assertAlmostEqual(coming[0]['predicted_units'], 36.0)

    def test_the_forecast_is_tenant_scoped(self):
        self.seed_six_day_business()
        other = self.product(self.biz_b, 'Other Rice', qty=5)
        self.close_days(self.biz_b, six_day_weeks(4), {other: 60})
        coming = [c for c in self.brain(self.admin)['coming'] if c['kind'] == 'velocity']
        self.assertEqual([c['product_name'] for c in coming], ['Rice 50kg'])
        self.assertAlmostEqual(coming[0]['predicted_units'], 36.0)


# ============================================================ UX-008 / UX-009 (#56)
class ForecastPresentationTests(BatchEBase):
    def test_units_are_whole_for_countable_goods(self):
        self.assertEqual(main.forecast_units_text(65.76), 'about 66 units')
        self.assertEqual(main.forecast_units_text(1.2), 'about 1 unit')
        self.assertEqual(main.forecast_units_text(0.3), 'less than 1 unit')
        self.assertEqual(main.forecast_units_text(0), 'no units')

    def test_api_keeps_precision_and_adds_whole_unit_wording(self):
        rice = self.product(self.biz_a, 'Rice', qty=5)
        # 5 weeks Mon–Sat = 30 days; 79 units -> 79 / 30 × 6 = 15.8 -> "about 16 units"
        dates = six_day_weeks(5)
        for i, d in enumerate(dates):
            self.close_days(self.biz_a, [d], {rice: 3 if i < 19 else 2})
        coming = [c for c in self.brain(self.admin)['coming'] if c['kind'] == 'velocity'][0]
        self.assertAlmostEqual(coming['predicted_units'], 15.8)
        self.assertEqual(coming['predicted_units_text'], 'about 16 units')

    def test_confidence_rule(self):
        label = lambda *a: main._brain_confidence_label(main._brain_confidence(*a))
        self.assertEqual(label(8, None, 0), 'Limited confidence')         # sparse, unchecked
        self.assertEqual(label(28, None, 0), 'Moderate confidence')       # enough history, unchecked
        self.assertEqual(label(602, None, 0), 'Moderate confidence')      # history alone never reads High
        self.assertEqual(label(602, 0.9, 2), 'Moderate confidence')       # too few checked forecasts
        self.assertEqual(label(602, 0.8, 5), 'High confidence')           # proven record
        self.assertEqual(label(602, 0.4, 5), 'Moderate confidence')
        self.assertEqual(label(602, 0.006, 15), 'Limited confidence')     # QA Tenant A's measured 0.6%

    def test_sparse_business_is_not_high_confidence(self):
        rice = self.product(self.biz_a, 'Rice', qty=5)
        self.close_days(self.biz_a, six_day_weeks(2), {rice: 2})  # 12 business days
        data = self.brain(self.admin)
        self.assertEqual({c['confidence'] for c in data['coming']}, {'Limited confidence'})
        self.assertIn('not yet been checked', data['confidence_basis'])

    def test_stronger_proven_data_can_be_high(self):
        rice = self.product(self.biz_a, 'Rice', qty=5)
        self.close_days(self.biz_a, six_day_weeks(6), {rice: 2})  # 36 business days
        now = datetime.utcnow()
        for i in range(4):
            self.db.add(main.BusinessBrainPrediction(
                business_id=self.biz_a.id, product_id=rice.id, kind='velocity', forecast_at=now - timedelta(days=40 + i),
                target_at=now - timedelta(days=33 + i), horizon_days=7, predicted_units=12, confidence=0.5,
                actual_units=11, accuracy_score=0.92, evaluated_at=now - timedelta(days=30 - i)))
        self.db.commit()
        data = self.brain(self.admin)
        velocity = [c for c in data['coming'] if c['kind'] == 'velocity'][0]
        self.assertEqual(velocity['confidence'], 'High confidence')
        self.assertIn('92%', data['confidence_basis'])

    def test_an_old_stored_high_score_is_not_shown(self):
        """A row saved under the old rule (0.90) is labelled with today's evidence."""
        rice = self.product(self.biz_a, 'Rice', qty=5)
        self.close_days(self.biz_a, six_day_weeks(2), {rice: 2})
        self.brain(self.admin)
        self.db.query(main.BusinessBrainPrediction).update({main.BusinessBrainPrediction.confidence: 0.90})
        self.db.commit()
        velocity = [c for c in self.brain(self.admin)['coming'] if c['kind'] == 'velocity'][0]
        self.assertEqual(velocity['confidence'], 'Limited confidence')


# ============================================================ BRAIN-003 (#55)
class RecommendationStateTests(BatchEBase):
    def test_learning_business_says_history_is_insufficient(self):
        rice = self.product(self.biz_a, 'Rice', qty=50)
        self.close_days(self.biz_a, [MONDAY, MONDAY + timedelta(1)], {rice: 1})
        self.assertEqual(self.brain(self.admin)['recommendation_state'], 'insufficient_history')

    def test_mature_business_with_nothing_found_says_what_was_checked(self):
        rice = self.product(self.biz_a, 'Rice', qty=500)
        self.close_days(self.biz_a, six_day_weeks(2), {rice: 1})
        data = self.brain(self.admin)
        self.assertEqual(data['recommendation_state'], 'none_from_checks')
        self.assertEqual(data['recommendation_checks'], ['stock levels', 'expected demand', 'seasonal demand'])

    def test_non_critical_recommendation_is_available(self):
        rice = self.product(self.biz_a, 'Rice', qty=30)
        self.close_days(self.biz_a, six_day_weeks(4), {rice: 6})  # 36 expected > 30; 5.8 days -> "important"
        data = self.brain(self.admin)
        self.assertEqual(data['recommendation_state'], 'available')
        self.assertTrue(any(r['priority'] == 'important' for r in data['recommendations']))

    def test_staff_restricted_view_says_so_and_gets_no_accuracy(self):
        rice = self.product(self.biz_a, 'Rice', qty=500)
        self.close_days(self.biz_a, six_day_weeks(2), {rice: 1})
        data = self.brain(self.staff)
        self.assertEqual(data['recommendation_state'], 'restricted')
        for withheld in ('memory', 'coming', 'outcomes', 'confidence', 'confidence_basis'):
            self.assertNotIn(withheld, data)

    def test_ui_no_longer_claims_no_recommendation_is_needed(self):
        self.assertNotIn('No supported recommendation is needed', APP_JS)
        self.assertNotIn('noCurrentRecommendation")', APP_JS)
        self.assertIn('recommendation_state', APP_JS)
        for key in ('recommendNoneFromChecks', 'recommendInsufficientHistory', 'recommendRestricted'):
            self.assertIn(f't("businessBrain.{key}")', APP_JS)


# ============================================================ UX-010 / UX-011 (#57)
class RecommendationValueTests(BatchEBase):
    def test_invalid_product_figures_are_left_out_and_reported(self):
        bad = self.product(self.biz_a, 'Negative Trace', qty=-40, retail=-900.0, cost=1.0, wholesale=-765.0)
        good = self.product(self.biz_a, 'Rice', qty=30, retail=46000.0)
        self.close_days(self.biz_a, six_day_weeks(4), {good: 6, bad: 1})
        body = self.client.get('/products/financial-intelligence', headers=self.auth(self.admin)).json()
        self.assertEqual(body['invalid_products'], 1)
        self.assertTrue(any('below zero' in n for n in body['notes']))
        bad_row = [p for p in body['products'] if p['name'] == 'Negative Trace'][0]
        self.assertTrue(bad_row['figures_invalid'])
        for group in ('money_at_risk', 'money_tied_up', 'margin_pressure', 'potentially_recoverable'):
            self.assertNotIn('Negative Trace', [p['name'] for p in body[group]['products']])

    def test_a_one_naira_recovery_is_not_a_recommendation(self):
        """₦1 of slow stock (1 unit × ₦1) against ₦2,400,000 of stock on hand
        (60 × ₦40,000) is 0.00004% — below the 1% materiality floor."""
        self.product(self.biz_a, 'Rice', qty=60, retail=46000.0, cost=40000.0)
        rice = self.db.query(main.Product).filter_by(name='Rice').one()
        self.product(self.biz_a, 'Test Item', qty=1, retail=2.0, cost=1.0, wholesale=1.5)
        self.close_days(self.biz_a, six_day_weeks(2), {rice: 1})
        self.sale(self.biz_a, rice, 1, datetime.utcnow() - timedelta(days=1)); self.db.commit()  # Rice is moving
        rec = self.client.get('/products/financial-intelligence', headers=self.auth(self.admin)).json()['potentially_recoverable']
        self.assertEqual(rec['total'], 1.0)            # still listed in the full analysis
        self.assertFalse(rec['recommendable'])        # but never offered as a recommendation

    def test_a_material_recovery_is_recommendable(self):
        self.product(self.biz_a, 'Rice', qty=10, retail=46000.0, cost=40000.0)
        rice = self.db.query(main.Product).filter_by(name='Rice').one()
        self.product(self.biz_a, 'Idle Stock', qty=10, retail=30000.0, cost=20000.0)
        self.close_days(self.biz_a, six_day_weeks(2), {rice: 1})
        self.sale(self.biz_a, rice, 1, datetime.utcnow() - timedelta(days=1)); self.db.commit()  # Rice is moving
        rec = self.client.get('/products/financial-intelligence', headers=self.auth(self.admin)).json()['potentially_recoverable']
        self.assertEqual(rec['total'], 200000.0)
        self.assertTrue(rec['recommendable'])

    def test_negative_stock_is_not_extra_revenue_at_risk(self):
        oil = self.product(self.biz_a, 'Oil', qty=0, retail=1000.0)
        self.close_days(self.biz_a, six_day_weeks(4), {oil: 6})  # expects 36
        self.brain(self.admin)
        rec = self.db.query(main.BusinessBrainRecommendation).filter_by(business_id=self.biz_a.id, kind='forecast_stockout').one()
        evidence = json.loads(rec.evidence_json); evidence['current_stock'] = -40
        rec.evidence_json = json.dumps(evidence); self.db.commit()
        risk = main._brain_revenue_at_risk(self.db, self.biz_a.id)
        self.assertAlmostEqual(risk['value'], 36000.0)  # 36 × ₦1,000, not 76 × ₦1,000

    def test_ui_uses_the_materiality_flag_and_plain_learning_labels(self):
        self.assertIn('recoverableGroup?.recommendable === false', APP_JS)
        self.assertIn('forecastsChecked: "Forecasts checked"', APP_JS)
        self.assertNotIn('predictionsConfirmed: "Predictions confirmed"', APP_JS)
        self.assertIn('evaluated || t("businessBrain.noneYet")', APP_JS)


# ============================================================ X6 (#60)
class BusinessBrainPermissionTests(BatchEBase):
    def test_ai_use_revoked_is_refused_on_both_endpoints(self):
        self.staff.permission_overrides = json.dumps({'ai.use': False}); self.db.commit()
        for path in ('/business-brain', '/business-brain/history'):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path, headers=self.auth(self.staff)).status_code, 403)

    def test_default_roles_still_reach_it(self):
        for user in (self.admin, self.manager, self.staff):
            with self.subTest(role=user.role):
                self.assertEqual(self.client.get('/business-brain', headers=self.auth(user)).status_code, 200)
                self.assertEqual(self.client.get('/business-brain/history', headers=self.auth(user)).status_code, 200)

    def test_starter_plan_keeps_business_brain(self):
        self.set_sub(self.biz_a, plan='core')
        self.assertEqual(self.client.get('/business-brain', headers=self.auth(self.admin)).status_code, 200)

    def test_the_comment_no_longer_claims_a_missing_check(self):
        src = (ROOT / 'backend' / 'main.py').read_text(encoding='utf-8')
        self.assertNotIn("that's ai.use, checked above", src)


# ============================================================ AI-002 / AI-003 / UX-012 (#52–54)
class AIContextTests(BatchEBase):
    def setUp(self):
        super().setUp()
        self.calls = []

        def fake(system, prompt, usage_out=None):
            self.calls.append((system, prompt))
            return '### Stock\n- **Rice** is selling well.'
        patcher = mock.patch.object(main, 'gemini_text_response', side_effect=fake)
        patcher.start(); self.addCleanup(patcher.stop)

    def seed_sales(self):
        """Last month: Rice 2 × ₦46,000 = ₦92,000; Beans 3 × ₦1,250.50 = ₦3,751.50."""
        rice = self.product(self.biz_a, 'Rice', qty=20, retail=46000.0, cost=40000.0, wholesale=43000.0)
        beans = self.product(self.biz_a, 'Beans', qty=8, retail=1250.5, cost=900.0, wholesale=1000.0)
        start, _ = main.resolve_financial_period(self.biz_a, 'previous_month')
        when = start + timedelta(days=3, hours=4)
        self.sale(self.biz_a, rice, 2, when); self.sale(self.biz_a, beans, 3, when + timedelta(minutes=5))
        other = self.product(self.biz_b, 'Tenant B Secret Oil', qty=9, retail=77777.0)
        self.sale(self.biz_b, other, 5, when)
        self.db.commit()

    def ask(self, user, message='What were my top products by revenue last month?'):
        return self.client.post('/ai/chat', headers=self.auth(user), json={'message': message})

    def context_of_last_call(self):
        prompt = self.calls[-1][1]
        return json.loads(prompt.split('Business data: ', 1)[1])

    def test_money_is_written_in_the_business_currency(self):
        self.assertEqual(main.money_text(46000.0, 'NGN (₦)'), '₦46,000')
        self.assertEqual(main.money_text(46000.0, 'NGN'), '₦46,000')
        self.assertEqual(main.money_text(1250.5, 'NGN'), '₦1,250.50')
        self.assertEqual(main.money_text(-5, 'USD ($)'), '-$5')
        self.assertEqual(main.money_text('n/a', 'NGN'), '')

    def test_admin_sales_question_gets_scoped_formatted_sales(self):
        self.seed_sales()
        r = self.ask(self.admin)
        self.assertEqual(r.status_code, 200, r.text)
        ctx = self.context_of_last_call()
        last_month = ctx['sales_by_period']['last_month']['totals'][0]
        self.assertEqual(last_month['currency'], 'NGN')
        self.assertEqual(last_month['gross_sales'], '₦95,751.50')
        self.assertEqual(last_month['top_products_by_revenue'][0], {'product': 'Rice', 'units_sold': 2, 'revenue': '₦92,000'})
        rice = [p for p in ctx['inventory'] if p['product'] == 'Rice'][0]
        self.assertEqual(rice['retail_price'], '₦46,000')
        prompt = self.calls[-1][1]
        self.assertNotIn('46000.0', prompt)
        self.assertNotIn('Tenant B Secret Oil', prompt)   # tenant isolation
        self.assertNotIn('77777', prompt)

    def test_role_without_sales_reports_gets_no_sales_figures(self):
        self.seed_sales()
        self.assertEqual(self.ask(self.staff).status_code, 200)
        ctx = self.context_of_last_call()
        self.assertIsInstance(ctx['sales_by_period'], str)
        self.assertIn('withheld', ctx['sales_by_period'])
        self.assertNotIn('₦92,000', self.calls[-1][1])

    def test_granting_sales_reports_adds_the_sales_figures(self):
        self.seed_sales()
        self.grant(self.staff, 'reports.sales')
        self.assertEqual(self.ask(self.staff).status_code, 200)
        self.assertIsInstance(self.context_of_last_call()['sales_by_period'], dict)

    def test_unrelated_question_still_uses_only_the_business_data(self):
        self.seed_sales()
        self.assertEqual(self.ask(self.admin, 'What is the capital of France?').status_code, 200)
        system = self.calls[-1][0]
        self.assertIn('using only the business data provided', system)
        self.assertIn('If the data does not answer the question', system)
        self.assertIn("Sales figures aren't included in your access.", system)

    def test_stock_out_question_has_the_forecast(self):
        rice = self.product(self.biz_a, 'Rice', qty=30)
        self.close_days(self.biz_a, six_day_weeks(4), {rice: 6})
        self.ask(self.admin, 'Which product runs out first?')
        row = self.context_of_last_call()['inventory'][0]
        self.assertEqual(row['expected_units_next_7_days'], 36)
        self.assertEqual(row['estimated_days_until_out_of_stock'], 5.8)

    def test_plan_and_permission_enforcement_is_unchanged(self):
        self.staff.permission_overrides = json.dumps({'ai.use': False}); self.db.commit()
        self.assertEqual(self.ask(self.staff).status_code, 403)
        self.set_sub(self.biz_a, plan='core')
        self.assertEqual(self.ask(self.admin).status_code, 403)
        self.assertEqual(self.client.get('/ai/insights', headers=self.auth(self.admin)).status_code, 403)
        self.assertEqual(self.calls, [])  # the model was never called

    def test_a_successful_answer_still_consumes_credits(self):
        before = self.db.query(main.AIUsageLedger).filter_by(business_id=self.biz_a.id).count()
        r = self.ask(self.admin)
        self.assertEqual(r.status_code, 200)
        self.assertGreater(r.json()['credits_consumed'], 0)
        self.assertEqual(self.db.query(main.AIUsageLedger).filter_by(business_id=self.biz_a.id).count(), before + 1)

    def test_insights_use_the_same_context_and_business_wording(self):
        self.seed_sales()
        r = self.client.get('/ai/insights', headers=self.auth(self.admin))
        self.assertEqual(r.status_code, 200, r.text)
        system, prompt = self.calls[-1]
        self.assertNotIn('intelligence engine', system)
        self.assertIn("Never call the business's records 'system anomalies', 'telemetry'", system)
        self.assertIn('physical count', system)
        self.assertIn('₦46,000', prompt)

    def test_customer_facing_loading_text_has_no_engineering_words(self):
        self.assertIn('analyzingTelemetry: "Reviewing your stock and sales…"', APP_JS)
        self.assertIn('analyzing: "Loading your analysis…"', APP_JS)
        self.assertNotIn('defaultInsight: "Inventory levels are stable.', APP_JS)


# ============================================================ UX-013 / UX-002 (#58–59)
class AccessibilityAndNamingTests(unittest.TestCase):
    def ai_center(self):
        start = INDEX_HTML.index('data-i18n="aiCenter.priceMonitorTitle"')
        return INDEX_HTML[INDEX_HTML.rfind('<div class="grid', 0, start):INDEX_HTML.index('data-i18n="aiCenter.closeAiCenter"')]

    def test_generate_insights_is_a_real_button(self):
        block = self.ai_center()
        self.assertEqual(block.count('<button type="button"'), 4)  # three cards + Close
        self.assertEqual(block.count('class="ai-center-card '), 3)
        self.assertNotIn('<div class="p-4 rounded-xl', block)
        self.assertRegex(block, r'<button type="button" data-ai-insights-trigger[^>]*onclick="closeAICenterModal\(\); fetchAIInsights\(\);"')
        self.assertIn('.ai-center-card:focus-visible{outline:2px solid #436BEE', BASE_CSS)

    def test_opening_the_ai_center_moves_keyboard_focus_into_it(self):
        self.assertIn('setTimeout(() => document.querySelector("#ai-center-modal .ai-center-card")?.focus(), 0);', APP_JS)
        self.assertIn('aiCenterOpener = document.activeElement;', APP_JS)

    def test_no_duplicate_insight_requests(self):
        self.assertIn('if (aiInsightsInFlight) return;', APP_JS)
        self.assertIn("el.disabled = true; el.setAttribute('aria-busy', 'true')", APP_JS)

    def test_one_name_for_inventory_financial_analysis(self):
        nav = re.search(r'id="nav-btn-predictive".*?</button>', INDEX_HTML, re.S).group(0)
        self.assertIn('data-i18n="predictive.title"', nav)
        self.assertIn('data-i18n="predictive.title">Inventory Financial Analysis</span></h3>', INDEX_HTML)
        self.assertIn('data-i18n="predictive.title">Inventory Financial Analysis</span>', self.ai_center())
        self.assertIn("'predictive monitor': 'predictive.title'", APP_JS)
        self.assertNotIn('Open Financial Intelligence', APP_JS)
        self.assertNotIn('aiCenter.predictiveTitle', INDEX_HTML)


if __name__ == '__main__':
    unittest.main()
