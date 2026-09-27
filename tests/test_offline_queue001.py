"""OFFLINE-QUEUE-001 (triage 78): a refused offline change keeps a useful reason.

A replayed change that fails the server's field validation used to be stored
as "The original operation contains invalid values." It now names the field
and the problem, and never echoes the submitted value. (The client half - a
later account/permission refusal no longer overwrites earlier refusal reasons -
is covered in tests/offline-browser-harness.html.)

Subprocess-isolated import of `main`, no database needed. The replay endpoint
itself is exercised in tests/test_offline_queue001_postgres.py.
"""
import os
os.environ['PYTHON_DOTENV_DISABLED'] = '1'
os.environ['SUPPLY_AI_SKIP_DB_STARTUP_CHECK'] = 'true'
os.environ['DATABASE_URL'] = 'postgresql+psycopg://test:test@127.0.0.1:65432/cauldra_test'
os.environ['SUPPLY_AI_SECRET_KEY'] = 'isolated-test-secret-012345678901234567890123456789'
import unittest
from pydantic import ValidationError
import main
import offline_access


def _error(model, **values):
    try:
        model(**values)
    except ValidationError as exc:
        return exc
    raise AssertionError("expected a validation error")


class ValidationReasonTests(unittest.TestCase):
    def test_names_the_field_and_problem(self):
        exc = _error(main.SalesCheckoutRequest, items=[{"product_id": 1, "quantity": "several-9137"}])
        message, fields = offline_access.validation_reason(exc)
        self.assertIn("invalid quantity", message)
        self.assertIn("integer", message)
        self.assertEqual(fields[0]["field"], "items.0.quantity")
        self.assertNotIn("several-9137", message + repr(fields), "the submitted value is never echoed")
        self.assertNotEqual(message, "The original operation contains invalid values.")

    def test_counts_further_problems(self):
        exc = _error(main.ProductCreate, name="Rice", quantity="x", min_stock_level="y", cost_price=1, retail_price=2)
        message, fields = offline_access.validation_reason(exc)
        self.assertGreaterEqual(len(fields), 3)  # category missing + two bad integers
        self.assertIn(f"{len(fields) - 1} more values also need review", message)
        self.assertEqual({f["field"] for f in fields} >= {"category", "quantity", "min_stock_level"}, True)


if __name__ == "__main__":
    unittest.main()
