import unittest

from fastapi import HTTPException
from pydantic import ValidationError

from backend.main import WarehouseCreate, WarehouseUpdate, resolve_location_context


class LocationAuthorityTests(unittest.TestCase):
    def test_nigeria_derives_ngn_and_lagos_timezone(self):
        result = resolve_location_context("Nigeria", "NG", "Lagos State", "Lagos")
        self.assertEqual(result["currency"], "NGN")
        self.assertEqual(result["timezone"], "Africa/Lagos")
        self.assertEqual(result["phone_country_code"], "+234")

    def test_country_and_code_contradiction_is_rejected(self):
        with self.assertRaises(HTTPException) as raised:
            resolve_location_context("Nigeria", "GH", None, "Accra")
        self.assertEqual(raised.exception.status_code, 400)

    def test_multitimezone_country_uses_region(self):
        result = resolve_location_context("United States", "US", "California", "San Francisco")
        self.assertEqual(result["timezone"], "America/Los_Angeles")
        with self.assertRaises(HTTPException):
            resolve_location_context("United States", "US", None, None)

    def test_warehouse_location_is_required(self):
        with self.assertRaises(ValidationError):
            WarehouseCreate(name="West Stockroom")
        with self.assertRaises(ValidationError):
            WarehouseUpdate(name="West Stockroom")


if __name__ == "__main__":
    unittest.main()
