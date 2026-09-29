"""POLISH batch (2026-09-29) — the backend's to_e164() stays the authoritative
phone check behind the new country-aware phone fields. The frontend now sends
canonical E.164 (tests/test_ui_input_polish.cjs); these cases pin what the
backend accepts, rejects and stores for the same numbers, so the two sides
cannot quietly drift apart. Example numbers come from Google's metadata
(phonenumbers.example_number_for_type), not hand-written lengths."""
import os
os.environ['PYTHON_DOTENV_DISABLED'] = '1'
os.environ['SUPPLY_AI_SKIP_DB_STARTUP_CHECK'] = 'true'
os.environ['DATABASE_URL'] = 'postgresql+psycopg://test:test@127.0.0.1:65432/cauldra_test'
os.environ['SUPPLY_AI_SECRET_KEY'] = 'isolated-test-secret-012345678901234567890123456789'
import unittest

import phonenumbers
from phonenumbers import PhoneNumberFormat, PhoneNumberType
from fastapi import HTTPException

import main

COUNTRIES = ["NG", "US", "GB", "FR", "ES", "PT"]


class PhoneCountryNormalizationTests(unittest.TestCase):
    def rejects(self, raw, region):
        with self.assertRaises(HTTPException) as ctx:
            main.to_e164(raw, region)
        self.assertEqual(ctx.exception.status_code, 400)

    def test_metadata_examples_normalize_to_e164_for_each_country(self):
        for region in COUNTRIES:
            for kind in (PhoneNumberType.MOBILE, PhoneNumberType.FIXED_LINE):
                example = phonenumbers.example_number_for_type(region, kind)
                e164 = phonenumbers.format_number(example, PhoneNumberFormat.E164)
                national = phonenumbers.format_number(example, PhoneNumberFormat.NATIONAL)
                with self.subTest(region=region, kind=kind):
                    self.assertEqual(main.to_e164(national, region), e164)                          # formatted as displayed
                    self.assertEqual(main.to_e164("".join(c for c in national if c.isdigit()), region), e164)  # digits only
                    self.assertEqual(main.to_e164(e164, region), e164)                              # already canonical (what the frontend now sends)

    def test_nigeria_domestic_zero_and_country_code(self):
        self.assertEqual(main.to_e164("08031234567", "NG"), "+2348031234567")
        self.assertEqual(main.to_e164("0803 123 4567", "NG"), "+2348031234567")
        # The old "<prefix> <typed national>" shape: the extra 0 is not kept.
        self.assertEqual(main.to_e164("+234 0803 123 4567", "NG"), "+2348031234567")
        self.assertEqual(main.to_e164("0023408031234567", "NG"), "+2348031234567")
        # A fixed line and a mobile of different digit counts are both valid — no global length rule.
        self.assertEqual(main.to_e164("02033 12 3456", "NG"), "+2342033123456")

    def test_international_number_keeps_its_own_code(self):
        self.assertEqual(main.to_e164("+44 7400 123456", "NG"), "+447400123456")
        self.assertEqual(main.to_e164("0044 7400 123456", "NG"), "+447400123456")

    def test_duplicated_prefix_short_long_and_impossible_numbers_are_rejected(self):
        self.rejects("+234 +44 7400 123456", "NG")  # what the old registration concatenation produced for a pasted number
        self.rejects("0803 123", "NG")                # too short
        self.rejects("0803 123 4567 8901 23", "NG")   # too long
        self.rejects("0100 000 0000", "NG")           # right length, impossible pattern
        self.rejects("1115550123", "US")              # invalid area code
        self.rejects("06 12 34 56", "FR")
        self.rejects("call the shop", "NG")


if __name__ == "__main__":
    unittest.main()
