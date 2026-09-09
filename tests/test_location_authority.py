import ast
import datetime as dt
import importlib.resources
import unittest
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from pydantic import ValidationError

from backend.main import (
    COUNTRY_CONTEXTS,
    _LOCATION_CITY_TIMEZONES,
    _LOCATION_TIMEZONE_OVERRIDES,
    _MULTI_TIMEZONE_COUNTRY_CODES,
    LocationCreate,
    WarehouseCreate,
    WarehouseUpdate,
    resolve_location_context,
)


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

    def test_us_new_york_and_missing_geography(self):
        result = resolve_location_context("United States", "US", "New York", "New York City")
        self.assertEqual(result["timezone"], "America/New_York")
        with self.assertRaises(HTTPException):
            resolve_location_context("United States", "US", None, None)

    def test_brazil_requires_and_normalizes_exact_geography(self):
        result = resolve_location_context("Brazil", "BR", "  SÃO   PAULO ", "Campinas")
        self.assertEqual(result["timezone"], "America/Sao_Paulo")
        with self.assertRaises(HTTPException):
            resolve_location_context("Brazil", "BR", None, None)

    def test_other_multitimezone_country_is_exact(self):
        result = resolve_location_context("Indonesia", "ID", "Bali", "Denpasar")
        self.assertEqual(result["timezone"], "Asia/Makassar")

    def test_single_timezone_country_remains_simple(self):
        result = resolve_location_context("Iceland", "IS", None, None)
        self.assertEqual(result["timezone"], "Atlantic/Reykjavik")

    def test_all_audited_multitimezone_countries_reject_missing_geography(self):
        self.assertEqual(
            _MULTI_TIMEZONE_COUNTRY_CODES,
            {"AU", "BR", "CA", "CD", "CL", "CN", "EC", "ES", "FM", "GL", "ID", "KI", "MN", "MX", "NZ", "PF", "PG", "PT", "RU", "UA", "US"},
        )
        self.assertEqual(_MULTI_TIMEZONE_COUNTRY_CODES, set(_LOCATION_TIMEZONE_OVERRIDES))
        for code in _MULTI_TIMEZONE_COUNTRY_CODES:
            with self.subTest(code=code), self.assertRaises(HTTPException):
                resolve_location_context(COUNTRY_CONTEXTS[code]["name"], code, None, None)

    def test_all_configured_timezone_aliases_are_valid_iana_names(self):
        zones = {
            timezone_name
            for mapping in (*_LOCATION_TIMEZONE_OVERRIDES.values(), *_LOCATION_CITY_TIMEZONES.values())
            for timezone_name in mapping.values()
        }
        for timezone_name in zones:
            with self.subTest(timezone=timezone_name):
                ZoneInfo(timezone_name)

    def test_supported_multitimezone_audit_matches_bundled_iana_data(self):
        table = importlib.resources.files("tzdata.zoneinfo").joinpath("zone.tab").read_text(encoding="utf-8")
        country_zones = {}
        for line in table.splitlines():
            if not line or line.startswith("#"):
                continue
            code, _, timezone_name, *_ = line.split("\t")
            country_zones.setdefault(code, []).append(timezone_name)
        sample_dates = [dt.datetime(year, month, 1, 12, tzinfo=dt.timezone.utc) for year in (2026, 2027, 2028) for month in range(1, 13)]
        operationally_multi = set()
        for code in COUNTRY_CONTEXTS:
            signatures = {
                tuple((instant.astimezone(ZoneInfo(zone)).utcoffset(), instant.astimezone(ZoneInfo(zone)).dst()) for instant in sample_dates)
                for zone in country_zones.get(code, [])
            }
            if len(signatures) > 1:
                operationally_multi.add(code)
        self.assertEqual(operationally_multi, set(_MULTI_TIMEZONE_COUNTRY_CODES))

    def test_client_timezone_field_is_retained_only_for_route_rejection(self):
        stale_client = LocationCreate(name="Lagos", country="Nigeria", country_code="NG", timezone="UTC")
        self.assertEqual(stale_client.timezone, "UTC")
        source = Path("backend/main.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        create_location = next(node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "create_location")
        create_source = ast.get_source_segment(source, create_location)
        self.assertIn("data.currency is not None or data.timezone is not None", create_source)

    def test_warehouse_location_is_required(self):
        with self.assertRaises(ValidationError):
            WarehouseCreate(name="West Stockroom")
        with self.assertRaises(ValidationError):
            WarehouseUpdate(name="West Stockroom")


if __name__ == "__main__":
    unittest.main()
