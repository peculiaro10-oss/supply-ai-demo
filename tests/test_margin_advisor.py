"""AI-004 — the AI Margin Advisor must accept what the product sends, and the
figures it returns must be prices, not the first two numbers in the prose.

No network: the request contract is checked against the real Pydantic model and
the figure extraction against the real helper.
"""
import os
os.environ['PYTHON_DOTENV_DISABLED'] = '1'
os.environ['SUPPLY_AI_SKIP_DB_STARTUP_CHECK'] = 'true'
os.environ['DATABASE_URL'] = 'postgresql+psycopg://test:test@127.0.0.1:65432/cauldra_test'
os.environ['SUPPLY_AI_SECRET_KEY'] = 'isolated-test-secret-012345678901234567890123456789'
import unittest
import pydantic
import main


class MarginRequestContractTests(unittest.TestCase):
    """The shipped clients send product_name; the canonical field is name."""

    def test_shipped_frontend_payload_is_accepted(self):
        req = main.MarginRequest(**{'product_name': 'AUDIT Bar Soap 12pk', 'category': 'Household',
                                    'cost_price': 5400.0, 'retail_price': 7000.0})
        self.assertEqual(req.name, 'AUDIT Bar Soap 12pk')
        self.assertEqual(req.cost_price, 5400.0)
        self.assertEqual(req.retail_price, 7000.0)

    def test_canonical_payload_is_accepted(self):
        req = main.MarginRequest(name='AUDIT Sugar 1kg', category='Groceries', cost_price=1150.0)
        self.assertEqual(req.name, 'AUDIT Sugar 1kg')
        self.assertIsNone(req.retail_price)

    def test_a_name_is_still_required(self):
        with self.assertRaises(pydantic.ValidationError):
            main.MarginRequest(category='Groceries', cost_price=1150.0)

    def test_category_and_cost_price_are_still_required(self):
        with self.assertRaises(pydantic.ValidationError):
            main.MarginRequest(name='X', cost_price=10.0)
        with self.assertRaises(pydantic.ValidationError):
            main.MarginRequest(name='X', category='Y')


class MarginFigureTests(unittest.TestCase):
    """The regression the audit caught: figures scraped out of prose."""

    def test_quoted_figures_are_used(self):
        advice = ('Price it competitively.\n'
                  'CAULDRA_PRICES: wholesale=6100; retail=7600')
        w, r, text = main.margin_figures(advice, 5400.0)
        self.assertEqual((w, r), (6100.0, 7600.0))
        self.assertNotIn('CAULDRA_PRICES', text)
        self.assertIn('Price it competitively.', text)

    def test_thousands_separators_survive(self):
        w, r, _ = main.margin_figures('CAULDRA_PRICES: wholesale=5,900.50; retail=7,250.00', 5400.0)
        self.assertEqual((w, r), (5900.5, 7250.0))

    def test_a_number_in_the_product_name_is_not_a_price(self):
        """'AUDIT Bar Soap 12pk' used to make both figures 12."""
        advice = ('For the AUDIT Bar Soap 12pk in Household, a 12pk carton moves fast.\n'
                  'CAULDRA_PRICES: wholesale=6100; retail=7600')
        w, r, _ = main.margin_figures(advice, 5400.0)
        self.assertEqual((w, r), (6100.0, 7600.0))

    def test_prose_is_never_scraped_when_the_line_is_missing(self):
        advice = 'I recommend wholesale at 5,400.00 and retail at 7,000.00.'
        w, r, text = main.margin_figures(advice, 5400.0)
        self.assertEqual((w, r), (6210.0, 7020.0))  # the product's own cost-plus defaults
        self.assertEqual(text, advice)

    def test_implausible_figures_fall_back(self):
        for line in ('CAULDRA_PRICES: wholesale=12; retail=12',          # below cost
                     'CAULDRA_PRICES: wholesale=7600; retail=6100',      # retail under wholesale
                     'CAULDRA_PRICES: wholesale=0; retail=0'):
            w, r, _ = main.margin_figures(line, 5400.0)
            self.assertEqual((w, r), (6210.0, 7020.0), line)

    def test_retail_may_equal_wholesale_when_genuinely_advised(self):
        w, r, _ = main.margin_figures('CAULDRA_PRICES: wholesale=6000; retail=6000', 5400.0)
        self.assertEqual((w, r), (6000.0, 6000.0))

    def test_zero_cost_price_still_returns_the_quoted_figures(self):
        w, r, _ = main.margin_figures('CAULDRA_PRICES: wholesale=800; retail=1200', 0.0)
        self.assertEqual((w, r), (800.0, 1200.0))

    def test_advice_is_never_empty(self):
        _, _, text = main.margin_figures('CAULDRA_PRICES: wholesale=6100; retail=7600', 5400.0)
        self.assertTrue(text.strip())

    def test_a_50000_naira_product_is_never_advised_at_50_naira(self):
        """The exact customer-facing consequence the audit named."""
        advice = ('For a 50,000.00 sack, wholesale around 52,000.00 works.\n'
                  'CAULDRA_PRICES: wholesale=52000; retail=58000')
        w, r, _ = main.margin_figures(advice, 50000.0)
        self.assertGreaterEqual(w, 50000.0)
        self.assertGreaterEqual(r, w)


if __name__ == '__main__':
    unittest.main()
