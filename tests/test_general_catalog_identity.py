"""General Catalog identity hardening — integrity tests.

Two groups:
  - PURE-LOGIC tests (no database at all): identity-key rules, size
    normalization, and the anti-corruption merge rule. These always run and
    need nothing beyond `import main`.
  - POSTGRESQL-BACKED tests (skipped unless TEST_POSTGRES_ADMIN_URL is
    configured — see tests/postgres_test_support.py): full end-to-end
    behavior including the /catalog/barcode-lookup endpoint, concurrent
    inserts, and deletion/no-corruption guarantees. Every test creates its
    own uniquely-named businesses/users/products within ONE disposable
    schema shared by the whole module (created in setUpModule, dropped in
    tearDownModule — see postgres_test_support.create_postgres_test_schema),
    so nothing here ever touches real data, and the schema is always
    dropped, even if a test fails or raises. No test in this file ever
    contacts the real UPCitemdb API — every provider call is mocked.
"""
from __future__ import annotations

import inspect
import os
import re
import threading
import unittest
import uuid
from unittest.mock import patch

from postgres_test_support import ADMIN_URL, create_postgres_test_schema, drop_postgres_test_schema

# `main` is intentionally NOT imported at module level here. Doing a plain
# top-level `import main` would get cached by Python under whatever
# DATABASE_URL happened to be active at that moment — and since
# create_postgres_test_schema() below (used by the PostgreSQL-backed test
# class) also does `importlib.import_module("main")` expecting a genuinely
# FRESH module bound to the disposable test schema, a stale cached import
# here would make it silently reuse the wrong (or unreachable) database
# connection instead. setUpModule() below is the ONE place `main` gets
# bound, for both the pure-logic tests and the PostgreSQL-backed ones — see
# postgres_test_support.create_postgres_test_schema()'s own docstring,
# which warns about this exact pitfall.
main = None


# =============================================================================
# PURE-LOGIC TESTS — no database needed at all.
# =============================================================================
class NormalizeCatalogSizeTests(unittest.TestCase):
    """Task section 3: normalize equivalent measurements; never treat a
    vague word as a physical size."""

    def test_equivalent_volumes_normalize_to_the_same_string(self):
        self.assertEqual(main.normalize_catalog_size("50cl"), main.normalize_catalog_size("500ml"))
        self.assertEqual(main.normalize_catalog_size("0.5l"), main.normalize_catalog_size("500ml"))
        self.assertEqual(main.normalize_catalog_size("0.5L"), "500ml")
        self.assertEqual(main.normalize_catalog_size("1L"), "1000ml")
        self.assertEqual(main.normalize_catalog_size("500 ml"), "500ml")

    def test_equivalent_weights_normalize_to_the_same_string(self):
        self.assertEqual(main.normalize_catalog_size("1000g"), main.normalize_catalog_size("1kg"))
        self.assertEqual(main.normalize_catalog_size("2kg"), "2000g")

    def test_different_sizes_do_not_normalize_the_same(self):
        self.assertNotEqual(main.normalize_catalog_size("500ml"), main.normalize_catalog_size("1L"))
        self.assertNotEqual(main.normalize_catalog_size("400g"), main.normalize_catalog_size("900g"))

    def test_vague_words_are_never_treated_as_a_measurement(self):
        for word in ("small", "medium", "large", "big", "family", "regular", "Medium Coca-Cola", "jumbo"):
            self.assertIsNone(main.normalize_catalog_size(word), word)

    def test_blank_and_unparseable_input_returns_none(self):
        self.assertIsNone(main.normalize_catalog_size(None))
        self.assertIsNone(main.normalize_catalog_size(""))
        self.assertIsNone(main.normalize_catalog_size("bottle"))
        self.assertIsNone(main.normalize_catalog_size("   "))

    def test_zero_and_negative_values_are_rejected(self):
        for value in ("0ml", "0g", "-5ml", "-1kg", "-0.5l"):
            self.assertIsNone(main.normalize_catalog_size(value), value)

    def test_extreme_values_never_crash_and_are_rejected(self):
        """Regression for a real bug found during the strict integrity audit:
        a sufficiently long digit string makes float() overflow to inf
        without raising, and int(inf) then raised an unhandled
        OverflowError. Extreme values are also physically implausible for a
        single product identity entry and must never be treated as
        'verified'."""
        extreme_cases = (
            "9" * 400 + "ml",
            "1" + "0" * 20 + "kg",
            "99999999999999999999999999999999999999l",
        )
        for value in extreme_cases:
            self.assertIsNone(main.normalize_catalog_size(value), value)  # must not raise
        # A merely large-but-still-plausible bulk/wholesale size is fine;
        # the cutoff exists only to reject clearly-impossible input.
        self.assertEqual(main.normalize_catalog_size("500ml"), "500ml")

    def test_malformed_decimals_are_rejected_not_guessed(self):
        for value in ("5..5ml", "5,5,5ml", "..5ml", "5.ml"):
            self.assertIsNone(main.normalize_catalog_size(value), value)

    def test_unsupported_units_are_never_guessed(self):
        for value in ("5lbs", "5oz", "5pt", "5gal"):
            self.assertIsNone(main.normalize_catalog_size(value), value)


class GeneralCatalogKeyForTests(unittest.TestCase):
    """Task section 2/4/11: the exact-match identity key that decides
    auto-reuse — letters E, F, G, H, I from the testing requirements."""

    def test_barcode_is_authoritative_and_exact(self):
        self.assertEqual(main.general_catalog_key_for("5449000000996", "Anything", "Anything"), "barcode:5449000000996")

    def test_scenario_E_equivalent_size_units_reuse_identity(self):
        self.assertEqual(
            main.general_catalog_key_for(None, "Coca-Cola", "500ml"),
            main.general_catalog_key_for(None, "Coca Cola", "50cl"),
        )

    def test_scenario_F_different_sizes_remain_distinct(self):
        self.assertNotEqual(
            main.general_catalog_key_for(None, "Peak Milk", "400g"),
            main.general_catalog_key_for(None, "Peak Milk", "900g"),
        )

    def test_scenario_G_different_variants_remain_distinct(self):
        self.assertNotEqual(
            main.general_catalog_key_for(None, "Coca-Cola Original", "500ml"),
            main.general_catalog_key_for(None, "Coca-Cola Zero", "500ml"),
        )

    def test_scenario_H_category_cannot_affect_identity_by_construction(self):
        # category isn't even accepted as a parameter any more -- the OLD
        # bug was `name + category`; the fix removes category from the
        # identity function's signature entirely, so two products that
        # differ ONLY in category are structurally indistinguishable to it.
        params = list(inspect.signature(main.general_catalog_key_for).parameters)
        self.assertNotIn("category", params)

    def test_scenario_I_vague_size_terminology_does_not_merge(self):
        self.assertNotEqual(
            main.general_catalog_key_for(None, "Medium Coca-Cola", None),
            main.general_catalog_key_for(None, "Coca-Cola", "500ml"),
        )

    def test_manual_products_examples_from_spec_section_11(self):
        a = main.general_catalog_key_for(None, "Coca-Cola", "500ml")
        b = main.general_catalog_key_for(None, "Coca Cola", "50cl")
        self.assertEqual(a, b)
        self.assertNotEqual(a, main.general_catalog_key_for(None, "Coca-Cola Zero", "500ml"))
        self.assertNotEqual(a, main.general_catalog_key_for(None, "Coca-Cola", "1L"))
        self.assertNotEqual(main.general_catalog_key_for(None, "Medium Coca-Cola", None), a)

    def test_unverified_size_never_cross_merges_independent_submissions(self):
        """The known unknown-size problem found in the strict integrity
        audit: 'Peak Milk' with no verified size from two INDEPENDENT
        products must never collapse into one shared identity merely
        because the names match and neither has a verified size."""
        k1 = main.general_catalog_key_for(None, "Peak Milk", None, uncertain_disambiguator=101)
        k2 = main.general_catalog_key_for(None, "Peak Milk", None, uncertain_disambiguator=202)
        self.assertNotEqual(k1, k2)

    def test_unverified_size_same_origin_reuses_the_same_key(self):
        """The SAME originating product (same disambiguator, e.g. the same
        Product.id across repeated edits) must keep mapping to the SAME
        catalog row -- otherwise every edit of an unverified-size product
        would grow the catalog forever."""
        k1 = main.general_catalog_key_for(None, "Peak Milk", None, uncertain_disambiguator=101)
        k2 = main.general_catalog_key_for(None, "Peak Milk", None, uncertain_disambiguator=101)
        self.assertEqual(k1, k2)

    def test_vague_size_words_never_cross_merge_either(self):
        k1 = main.general_catalog_key_for(None, "Peak Milk", "large", uncertain_disambiguator=1)
        k2 = main.general_catalog_key_for(None, "Peak Milk", "family", uncertain_disambiguator=2)
        self.assertNotEqual(k1, k2)

    def test_missing_disambiguator_still_never_silently_merges(self):
        """Even if a future/hypothetical caller forgets to pass a
        disambiguator, the fallback must never be a fixed placeholder that
        could itself cause cross-submission merging."""
        k1 = main.general_catalog_key_for(None, "Peak Milk", None)
        k2 = main.general_catalog_key_for(None, "Peak Milk", None)
        self.assertNotEqual(k1, k2)

    def test_verified_size_merging_is_unaffected_by_the_disambiguator_fix(self):
        k1 = main.general_catalog_key_for(None, "Coca-Cola", "500ml", uncertain_disambiguator=1)
        k2 = main.general_catalog_key_for(None, "Coca Cola", "50cl", uncertain_disambiguator=2)
        self.assertEqual(k1, k2)


class SafeMergeTests(unittest.TestCase):
    """_general_catalog_apply_safe_update is pure Python-attribute logic —
    no session/flush/commit involved — so it is fully testable without a
    database. Task section 6 — letters K, O."""

    def _row(self, **overrides):
        defaults = dict(barcode=None, catalog_key="k", product_name="", size=None, brand=None, source="business_submission")
        defaults.update(overrides)
        return main.GeneralCatalog(**defaults)

    def test_fills_missing_fields(self):
        item = self._row()
        changed = main._general_catalog_apply_safe_update(item, product_name="Coca-Cola Original", brand=None, size="500ml", barcode=None, source="business_submission")
        self.assertTrue(changed)
        self.assertEqual(item.product_name, "Coca-Cola Original")
        self.assertEqual(item.size, "500ml")

    def test_scenario_K_business_edit_cannot_overwrite_established_canonical_identity(self):
        item = self._row(product_name="Coca-Cola Original", size="500ml")
        main._general_catalog_apply_safe_update(item, product_name="Cold Drink", brand=None, size="Medium", barcode=None, source="business_submission")
        self.assertEqual(item.product_name, "Coca-Cola Original")
        self.assertEqual(item.size, "500ml")

    def test_scenario_O_conflicting_information_is_silently_ignored_not_corrupted(self):
        item = self._row(product_name="Original Name", size="500ml", source="business_submission")
        changed = main._general_catalog_apply_safe_update(item, product_name="Conflicting Name", brand=None, size="1L", barcode=None, source="business_submission")
        self.assertFalse(changed)
        self.assertEqual(item.product_name, "Original Name")
        self.assertEqual(item.size, "500ml")

    def test_upcitemdb_may_correct_a_business_submission_value(self):
        item = self._row(barcode="123456", catalog_key="barcode:123456", product_name="typo", size="500ml", source="business_submission")
        main._general_catalog_apply_safe_update(item, product_name="Correct Name", brand="RealBrand", size="500ml", barcode="123456", source="upcitemdb")
        self.assertEqual(item.product_name, "Correct Name")
        self.assertEqual(item.brand, "RealBrand")
        self.assertEqual(item.source, "upcitemdb")

    def test_external_provider_confirmed_identity_is_never_degraded_by_a_later_business_edit(self):
        item = self._row(barcode="123456", catalog_key="barcode:123456", product_name="Correct Name", brand="RealBrand", size="500ml", source="upcitemdb")
        main._general_catalog_apply_safe_update(item, product_name="Some Other Name", brand=None, size=None, barcode="123456", source="business_submission")
        self.assertEqual(item.product_name, "Correct Name")
        self.assertEqual(item.source, "upcitemdb")


class PrivacyModelTests(unittest.TestCase):
    """Task section 10/18 — letter L."""

    def test_scenario_L_general_catalog_model_never_carries_business_sensitive_columns(self):
        columns = {c.name for c in main.GeneralCatalog.__table__.columns}
        forbidden = {
            "business_id", "business_name", "user_id", "owner_id", "cost_price",
            "wholesale_price", "retail_price", "quantity", "min_stock_level",
            "warehouse", "supplier", "sku", "sales_history", "purchase_history", "notes",
        }
        self.assertEqual(columns & forbidden, set())

    def test_general_catalog_get_endpoint_returns_only_safe_response_keys(self):
        # Static check of the literal response dicts search_general_catalog()
        # can return (not the whole function body — internal queries
        # legitimately reference business-scoped columns like
        # Product.business_id; what matters is what's ever sent back).
        source = inspect.getsource(main.search_general_catalog)
        return_dicts = re.findall(r"return\s*(\{.*?\})\s*$", source, re.MULTILINE | re.DOTALL)
        joined_returns = "\n".join(return_dicts)
        allowed_keys = {"found", "barcode", "product_name", "brand", "size"}
        found_keys = set(re.findall(r'"(\w+)"\s*:', joined_returns))
        self.assertTrue(found_keys, "expected to find at least one returned key")
        self.assertEqual(found_keys - allowed_keys, set())
        for forbidden in ("cost_price", "wholesale_price", "warehouse", "supplier", "sku", "quantity", "category", "business_id"):
            self.assertNotIn(forbidden, found_keys)

    def test_general_catalog_get_endpoint_requires_an_exact_barcode_not_a_bulk_listing(self):
        params = inspect.signature(main.search_general_catalog).parameters
        self.assertIn("barcode", params)
        # No pagination/limit/offset-style parameter that would suggest a
        # browseable listing exists on this endpoint.
        self.assertFalse({"limit", "offset", "page"} & set(params))


class NoDeletionPathTests(unittest.TestCase):
    """Task section 7 — letter J, static half: prove by source inspection
    that delete_product / resolve_product_deletion never touch GeneralCatalog
    at all (the PostgreSQL-backed test below proves it dynamically too)."""

    def test_delete_product_never_mentions_general_catalog(self):
        source = inspect.getsource(main.delete_product)
        self.assertNotIn("GeneralCatalog", source)

    def test_resolve_product_deletion_never_mentions_general_catalog(self):
        source = inspect.getsource(main.resolve_product_deletion)
        self.assertNotIn("GeneralCatalog", source)


# =============================================================================
# POSTGRESQL-BACKED TESTS
# One disposable schema for the whole module — see postgres_test_support.py.
# Skipped entirely (not failed) when TEST_POSTGRES_ADMIN_URL is not set.
# =============================================================================
_ctx = None
pg_main = None


def setUpModule():
    global _ctx, pg_main, main
    if not ADMIN_URL:
        # Pure-logic tests still need SOME importable `main` — a fake,
        # unreachable DATABASE_URL is fine since none of them touch a
        # database; SUPPLY_AI_SKIP_DB_STARTUP_CHECK keeps import-time
        # connectivity checks out of the way. setdefault() never overrides
        # a real value the caller already exported.
        os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://baduser:badpass@127.0.0.1:5432/nonexistent")
        os.environ.setdefault("SUPPLY_AI_SECRET_KEY", "test-secret-0123456789abcdef0123456789abcdef0123456789abcdef")
        os.environ.setdefault("SUPPLY_AI_SKIP_DB_STARTUP_CHECK", "true")
        os.environ.setdefault("SUPPLY_AI_ENV", "development")
        os.environ.setdefault("SUPPLY_AI_TRUSTED_HOSTS", "")
        import main as _main_module
        main = _main_module
        return
    _ctx = create_postgres_test_schema("cauldra_catalog")
    pg_main = _ctx.main
    main = pg_main  # pure-logic tests reuse the SAME correctly-bound module


def tearDownModule():
    if _ctx is not None:
        drop_postgres_test_schema(_ctx, "cauldra_catalog")


def _fake_upc_hit(product_name="Test Cola", brand="TestBrand", size="500ml"):
    return {"outcome": "hit", "identity": {"product_name": product_name, "brand": brand, "size": size}, "detail": None, "http_status": 200}


def _fake_upc_miss():
    return {"outcome": "miss", "identity": None, "detail": "no match", "http_status": 200}


def _fake_upc_temporary_error():
    return {"outcome": "temporary_error", "identity": None, "detail": "simulated timeout", "http_status": None}


@unittest.skipUnless(ADMIN_URL, "TEST_POSTGRES_ADMIN_URL is not configured")
class GeneralCatalogPostgresTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient

        suffix = uuid.uuid4().hex[:10]
        self.suffix = suffix
        db = pg_main.SessionLocal()
        biz_a = pg_main.BusinessProfile(business_code=f"GC-A-{suffix}", company_name="Catalog Test A")
        biz_b = pg_main.BusinessProfile(business_code=f"GC-B-{suffix}", company_name="Catalog Test B")
        db.add_all([biz_a, biz_b])
        db.flush()
        # The real /auth/register-business flow seeds this automatically
        # (see main.py) — creating BusinessProfile directly via the ORM here
        # bypasses that, so create_product()'s warehouse check would 400
        # without it.
        db.add_all([
            pg_main.Warehouse(business_id=biz_a.id, name="Main Central Warehouse", is_active=True),
            pg_main.Warehouse(business_id=biz_b.id, name="Main Central Warehouse", is_active=True),
        ])
        admin_a = pg_main.User(username=f"Admin A {suffix}", password=pg_main.hash_password("AdminPass9"), role="admin",
                                email=f"admina-{suffix}@test.com", phone="1", business_id=biz_a.id, disabled=False)
        admin_b = pg_main.User(username=f"Admin B {suffix}", password=pg_main.hash_password("AdminPass9"), role="admin",
                                email=f"adminb-{suffix}@test.com", phone="2", business_id=biz_b.id, disabled=False)
        db.add_all([admin_a, admin_b])
        db.commit()

        client = TestClient(pg_main.app)

        def login(business_code, username):
            r = client.post("/auth/admin-login", json={"business_id": business_code, "username": username, "password": "AdminPass9"})
            assert r.status_code == 200, r.text
            return r.json()["access_token"]

        self.db = db
        self.client = client
        self.biz_a, self.biz_b = biz_a, biz_b
        self.token_a = login(biz_a.business_code, admin_a.username)
        self.token_b = login(biz_b.business_code, admin_b.username)

    def tearDown(self):
        self.db.close()

    def auth(self, token):
        return {"Authorization": f"Bearer {token}"}

    def create_product(self, token, *, name, category="General", size=None, barcode=None, price=1000.0, client_ref=None):
        payload = {
            "name": name, "category": category, "size": size, "barcode": barcode,
            "quantity": 10, "min_stock_level": 1, "cost_price": price * 0.5, "retail_price": price,
        }
        if client_ref:
            payload["client_ref"] = client_ref
        r = self.client.post("/products/", json=payload, headers=self.auth(token))
        return r

    def catalog_rows_for_barcode(self, barcode):
        return self.db.query(pg_main.GeneralCatalog).filter(pg_main.GeneralCatalog.barcode == barcode).all()

    # -- A: existing barcode in General Catalog -> external API NOT called --
    def test_scenario_A_catalog_hit_never_calls_upcitemdb(self):
        barcode = f"{9000000000000 + int(self.suffix[:6], 16) % 900000}"[:13]
        self.db.add(pg_main.GeneralCatalog(barcode=barcode, catalog_key=f"barcode:{barcode}", product_name="Already Known", size="500ml", source="business_submission"))
        self.db.commit()

        with patch("upcitemdb_provider.lookup_upcitemdb_detailed", side_effect=AssertionError("UPCitemdb must not be called on a catalog hit")):
            r = self.client.post("/catalog/barcode-lookup", json={"barcode": barcode}, headers=self.auth(self.token_a))
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["source"], "cauldra_catalog")
        self.assertEqual(body["product_name"], "Already Known")

    # -- B: unknown barcode -> miss -> external called -> cached -> 2nd lookup catalog-only --
    def test_scenario_B_unknown_barcode_calls_upcitemdb_once_then_caches(self):
        barcode = f"{8000000000000 + int(self.suffix[:6], 16) % 900000}"[:13]
        with patch("upcitemdb_provider.lookup_upcitemdb_detailed", return_value=_fake_upc_hit(product_name="Freshly Found", size="330ml")) as mocked:
            r1 = self.client.post("/catalog/barcode-lookup", json={"barcode": barcode}, headers=self.auth(self.token_a))
            self.assertEqual(r1.status_code, 200, r1.text)
            self.assertEqual(r1.json()["source"], "upcitemdb")
            self.assertEqual(mocked.call_count, 1)

        rows = self.catalog_rows_for_barcode(barcode)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].product_name, "Freshly Found")
        self.assertEqual(rows[0].source, "upcitemdb")

        with patch("upcitemdb_provider.lookup_upcitemdb_detailed", side_effect=AssertionError("must not call UPCitemdb again")):
            r2 = self.client.post("/catalog/barcode-lookup", json={"barcode": barcode}, headers=self.auth(self.token_b))
        self.assertEqual(r2.status_code, 200, r2.text)
        self.assertEqual(r2.json()["source"], "cauldra_catalog")
        self.assertEqual(r2.json()["product_name"], "Freshly Found")

    # -- C: same barcode, two businesses -> one shared identity only --
    def test_scenario_C_two_businesses_same_barcode_share_one_catalog_identity(self):
        barcode = f"{7000000000000 + int(self.suffix[:6], 16) % 900000}"[:13]
        r1 = self.create_product(self.token_a, name="Shared Item", barcode=barcode)
        self.assertEqual(r1.status_code, 200, r1.text)
        r2 = self.create_product(self.token_b, name="Shared Item Renamed Locally", barcode=barcode)
        self.assertEqual(r2.status_code, 200, r2.text)

        rows = self.catalog_rows_for_barcode(barcode)
        self.assertEqual(len(rows), 1, "exactly one shared catalog identity must exist for this barcode")
        # business B's differently-named local product must NOT have overwritten it
        self.assertEqual(rows[0].product_name, "Shared Item")

    # -- D: concurrent same-barcode inserts -> no duplicate, no unhandled error --
    def test_scenario_D_concurrent_inserts_never_produce_a_duplicate_row(self):
        barcode = f"{6000000000000 + int(self.suffix[:6], 16) % 900000}"[:13]
        errors = []

        def worker():
            session = pg_main.SessionLocal()
            try:
                key = pg_main.general_catalog_key_for(barcode, "Race Product", "500ml")
                pg_main._general_catalog_atomic_get_or_create(
                    session, key=key, barcode=barcode, product_name="Race Product",
                    brand=None, size="500ml", source="business_submission",
                )
                session.commit()
            except Exception as exc:  # pragma: no cover - failure path is the point of the assertion below
                errors.append(exc)
                session.rollback()
            finally:
                session.close()

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(errors, [], f"concurrent upsert raised unhandled errors: {errors}")
        rows = self.catalog_rows_for_barcode(barcode)
        self.assertEqual(len(rows), 1, "concurrent first-submissions of the same barcode must resolve to exactly one row")

    # -- J: business deletion does not delete General Catalog row --
    def test_scenario_J_deleting_business_product_does_not_delete_catalog_row(self):
        barcode = f"{5000000000000 + int(self.suffix[:6], 16) % 900000}"[:13]
        r = self.create_product(self.token_a, name="Deletable Item", barcode=barcode)
        self.assertEqual(r.status_code, 200, r.text)
        product_id = r.json()["id"]
        self.assertEqual(len(self.catalog_rows_for_barcode(barcode)), 1)

        d = self.client.delete(f"/products/{product_id}", headers=self.auth(self.token_a))
        self.assertEqual(d.status_code, 200, d.text)

        rows = self.catalog_rows_for_barcode(barcode)
        self.assertEqual(len(rows), 1, "General Catalog identity must survive the business deleting its own product")

    # -- M: database constraints enforce uniqueness (not just app logic) --
    def test_scenario_M_database_rejects_a_direct_duplicate_barcode_insert(self):
        from sqlalchemy.exc import IntegrityError

        barcode = f"{4000000000000 + int(self.suffix[:6], 16) % 900000}"[:13]
        self.db.add(pg_main.GeneralCatalog(barcode=barcode, catalog_key=f"barcode:{barcode}", product_name="First", source="business_submission"))
        self.db.commit()

        self.db.add(pg_main.GeneralCatalog(barcode=barcode, catalog_key=f"barcode:{barcode}-dup", product_name="Second", source="business_submission"))
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()

    # -- N: exact existing canonical identity reused, not duplicated --
    def test_scenario_N_exact_no_barcode_identity_is_reused_across_businesses(self):
        r1 = self.create_product(self.token_a, name="Reusable Snack", size="200g")
        self.assertEqual(r1.status_code, 200, r1.text)
        r2 = self.create_product(self.token_b, name="Reusable Snack", size="200g")
        self.assertEqual(r2.status_code, 200, r2.text)

        key = pg_main.general_catalog_key_for(None, "Reusable Snack", "200g")
        rows = self.db.query(pg_main.GeneralCatalog).filter(pg_main.GeneralCatalog.catalog_key == key).all()
        self.assertEqual(len(rows), 1, "identical no-barcode identity from two businesses must not duplicate")

    # -- P: external provider error does not corrupt an unrelated existing catalog row --
    def test_scenario_P_provider_error_does_not_corrupt_an_existing_catalog_row(self):
        known_barcode = f"{3000000000000 + int(self.suffix[:6], 16) % 900000}"[:13]
        unknown_barcode = f"{3100000000000 + int(self.suffix[:6], 16) % 900000}"[:13]
        self.db.add(pg_main.GeneralCatalog(barcode=known_barcode, catalog_key=f"barcode:{known_barcode}", product_name="Untouchable", size="500ml", source="upcitemdb"))
        self.db.commit()

        with patch("upcitemdb_provider.lookup_upcitemdb_detailed", return_value=_fake_upc_temporary_error()):
            r = self.client.post("/catalog/barcode-lookup", json={"barcode": unknown_barcode}, headers=self.auth(self.token_a))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["source"], "upcitemdb_unavailable")

        self.db.expire_all()
        untouched = self.catalog_rows_for_barcode(known_barcode)[0]
        self.assertEqual(untouched.product_name, "Untouchable")
        self.assertEqual(untouched.size, "500ml")
        self.assertEqual(untouched.source, "upcitemdb")

    # -- Q: General Catalog query error is distinguished from a genuine miss --
    def test_scenario_Q_catalog_query_error_is_distinguished_from_a_genuine_miss(self):
        barcode = f"{2000000000000 + int(self.suffix[:6], 16) % 900000}"[:13]
        with patch("main.lookup_general_catalog", side_effect=RuntimeError("simulated DB hiccup")), \
             patch("upcitemdb_provider.lookup_upcitemdb_detailed", return_value=_fake_upc_miss()):
            r = self.client.post("/catalog/barcode-lookup", json={"barcode": barcode}, headers=self.auth(self.token_a))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["source"], "catalog_error")
        self.assertNotEqual(r.json()["source"], "not_found")

    # -- R: product create retry/offline replay does not create duplicate identities --
    def test_scenario_R_idempotent_replay_does_not_duplicate_the_catalog_row(self):
        barcode = f"{1000000000000 + int(self.suffix[:6], 16) % 900000}"[:13]
        client_ref = f"replay-{self.suffix}"
        r1 = self.create_product(self.token_a, name="Replayed Item", barcode=barcode, client_ref=client_ref)
        self.assertEqual(r1.status_code, 200, r1.text)
        r2 = self.create_product(self.token_a, name="Replayed Item", barcode=barcode, client_ref=client_ref)
        self.assertEqual(r2.status_code, 200, r2.text)
        self.assertEqual(r1.json()["id"], r2.json()["id"], "the same client_ref must replay, not duplicate, the product")

        rows = self.catalog_rows_for_barcode(barcode)
        self.assertEqual(len(rows), 1)
        products = self.db.query(pg_main.Product).filter(pg_main.Product.business_id == self.biz_a.id, pg_main.Product.barcode == barcode).all()
        self.assertEqual(len(products), 1)

    def no_barcode_rows_named(self, name):
        normalized = pg_main._dup_norm_name(name)
        return [
            r for r in self.db.query(pg_main.GeneralCatalog).filter(pg_main.GeneralCatalog.barcode.is_(None)).all()
            if pg_main._dup_norm_name(r.product_name) == normalized
        ]

    # =========================================================================
    # STRICT INTEGRITY AUDIT — additional scenarios
    # =========================================================================

    # -- The known unknown-size problem: independent submissions with no
    #    verified size must NEVER auto-merge merely because names match. --
    def test_unverified_size_same_name_different_businesses_does_not_merge(self):
        r1 = self.create_product(self.token_a, name="Audit Peak Milk", size=None)
        self.assertEqual(r1.status_code, 200, r1.text)
        r2 = self.create_product(self.token_b, name="Audit Peak Milk", size=None)
        self.assertEqual(r2.status_code, 200, r2.text)

        rows = self.no_barcode_rows_named("Audit Peak Milk")
        self.assertEqual(len(rows), 2, "two independent no-verified-size submissions must NOT share one canonical identity")

    def test_vague_size_words_do_not_merge_across_businesses(self):
        r1 = self.create_product(self.token_a, name="Audit Vague Widget", size="large")
        self.assertEqual(r1.status_code, 200, r1.text)
        r2 = self.create_product(self.token_b, name="Audit Vague Widget", size="family")
        self.assertEqual(r2.status_code, 200, r2.text)
        rows = self.no_barcode_rows_named("Audit Vague Widget")
        self.assertEqual(len(rows), 2)

    def test_unverified_size_same_product_reedited_reuses_the_same_row_not_growing_forever(self):
        created = self.create_product(self.token_a, name="Audit Re-Edited Widget", size=None)
        self.assertEqual(created.status_code, 200, created.text)
        product_id = created.json()["id"]

        for i in range(3):
            edit = self.client.patch(f"/products/{product_id}", json={"quantity": 5 + i}, headers=self.auth(self.token_a))
            self.assertEqual(edit.status_code, 200, edit.text)

        rows = self.no_barcode_rows_named("Audit Re-Edited Widget")
        self.assertEqual(len(rows), 1, "the SAME product re-edited must keep mapping to the SAME catalog row, not grow the catalog every edit")

    # -- Manager-approved deletion must also preserve the catalog row --
    def test_manager_approved_deletion_preserves_general_catalog(self):
        barcode = f"{4500000000000 + int(self.suffix[:6], 16) % 900000}"[:13]
        created = self.create_product(self.token_a, name="Manager Delete Target", barcode=barcode)
        self.assertEqual(created.status_code, 200, created.text)
        product_id = created.json()["id"]
        self.assertEqual(len(self.catalog_rows_for_barcode(barcode)), 1)

        manager = pg_main.User(
            username=f"Manager Del {self.suffix}", password=pg_main.hash_password("ManagerPass9"), role="manager",
            email=f"managerdel-{self.suffix}@test.com", phone="9", business_id=self.biz_a.id, disabled=False,
        )
        self.db.add(manager)
        self.db.commit()
        login = self.client.post("/auth/employee-login", json={
            "business_id": self.biz_a.business_code, "username": manager.username,
            "password": "ManagerPass9", "selected_role": "manager",
        })
        self.assertEqual(login.status_code, 200, login.text)
        manager_token = login.json()["access_token"]

        request = self.client.delete(f"/products/{product_id}", headers=self.auth(manager_token))
        self.assertEqual(request.status_code, 200, request.text)
        self.assertIn("approval", request.json().get("message", "").lower())

        pending = self.client.get("/product-deletion-requests", headers=self.auth(self.token_a))
        self.assertEqual(pending.status_code, 200, pending.text)
        matching = [r for r in pending.json() if r.get("product_name") == "Manager Delete Target"]
        self.assertEqual(len(matching), 1, pending.text)

        approve = self.client.post(f"/product-deletion-requests/{matching[0]['id']}/approve", headers=self.auth(self.token_a))
        self.assertEqual(approve.status_code, 200, approve.text)

        self.assertIsNone(self.db.query(pg_main.Product).filter(pg_main.Product.id == product_id).first())
        rows = self.catalog_rows_for_barcode(barcode)
        self.assertEqual(len(rows), 1, "General Catalog identity must survive manager-approved deletion")

    # -- Fuzzy similarity must only ever produce a candidate, never a merge --
    def test_fuzzy_candidates_are_suggestions_only_never_auto_merged(self):
        r1 = self.create_product(self.token_a, name="Audit Cream Crackers", size="200g")
        self.assertEqual(r1.status_code, 200, r1.text)
        r2 = self.create_product(self.token_b, name="Audit Blue Cream Crackers", size="200g")
        self.assertEqual(r2.status_code, 200, r2.text)

        rows_a = self.no_barcode_rows_named("Audit Cream Crackers")
        rows_b = self.no_barcode_rows_named("Audit Blue Cream Crackers")
        self.assertEqual(len(rows_a), 1)
        self.assertEqual(len(rows_b), 1)
        self.assertNotEqual(rows_a[0].catalog_key, rows_b[0].catalog_key, "similar-but-different names must never auto-merge")

        candidates = pg_main.find_general_catalog_candidates(self.db, "Audit Cream Crackers", "200g", limit=5)
        matched = [c for c in candidates if c["catalog_key"] == rows_b[0].catalog_key]
        self.assertEqual(len(matched), 1, "the similar existing row should still surface as a candidate for manual review")

    # -- General Catalog failure must never fail a valid Product creation --
    def test_general_catalog_failure_does_not_fail_product_creation(self):
        with patch("main._general_catalog_atomic_get_or_create", side_effect=RuntimeError("simulated catalog failure")):
            r = self.create_product(self.token_a, name="Resilient Despite Catalog Failure", size=None)
        self.assertEqual(r.status_code, 200, f"a General Catalog failure must never surface as a failed product creation: {r.text}")
        product_id = r.json()["id"]
        fetched = self.db.query(pg_main.Product).filter(pg_main.Product.id == product_id).first()
        self.assertIsNotNone(fetched, "the product must be genuinely persisted despite the catalog failure")
        # The outer session must remain fully usable after the SAVEPOINT
        # rollback -- prove it by successfully doing something else with it.
        self.db.add(pg_main.AuditLog(business_id=self.biz_a.id, action="TEST_PROBE", actor_username="test", description="post-failure session usability check"))
        self.db.commit()

    # -- POS/New Sale isolation regression --
    def test_pos_related_code_never_references_general_catalog_or_upcitemdb(self):
        import inspect
        pos_related_names = [
            name for name in dir(pg_main)
            if any(token in name.lower() for token in ("pos_", "sale_barcode", "resolve_scanned"))
        ]
        # Nothing in this backend module implements POS scanning server-side
        # at all (see catalog_barcode_lookup's own section comment) -- this
        # positively confirms that, rather than assuming it.
        for name in pos_related_names:
            obj = getattr(pg_main, name)
            if callable(obj):
                source = inspect.getsource(obj)
                self.assertNotIn("GeneralCatalog", source, name)
                self.assertNotIn("lookup_upcitemdb", source, name)

    # -- Concurrency: same barcode, conflicting submitted names, through the
    #    REAL HTTP endpoint (per-thread TestClient — a shared sync TestClient
    #    is not guaranteed safe to call concurrently from multiple threads;
    #    each worker below gets its own). Proves the full stack, not just
    #    the isolated helper function, never 500s under a real race. --
    def test_concurrent_same_barcode_conflicting_names_via_real_endpoint_never_500s(self):
        from fastapi.testclient import TestClient as _TestClient

        barcode = f"{4200000000000 + int(self.suffix[:6], 16) % 900000}"[:13]
        names = ["Race Name Alpha", "Race Name Beta"]
        tokens = [self.token_a, self.token_b]
        results, errors = [], []

        def worker(name, token):
            try:
                client = _TestClient(pg_main.app)
                r = client.post("/products/", json={
                    "name": name, "category": "General", "size": "1 unit", "barcode": barcode,
                    "warehouse": "Main Central Warehouse", "quantity": 1, "min_stock_level": 1,
                    "cost_price": 1.0, "wholesale_price": 1.0, "retail_price": 2.0,
                }, headers=self.auth(token))
                results.append(r)
            except Exception as exc:  # pragma: no cover - failure path is the assertion below
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(n, t)) for n, t in zip(names, tokens)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        self.assertEqual(errors, [], f"concurrent product creation raised: {errors}")
        self.assertEqual(len(results), 2)
        for r in results:
            self.assertEqual(r.status_code, 200, r.text)

        rows = self.catalog_rows_for_barcode(barcode)
        self.assertEqual(len(rows), 1, "exactly one canonical identity must survive a concurrent conflicting-name race")
        self.assertIn(rows[0].product_name, names)

    # -- Concurrency: business_submission racing UPCitemdb's own cache-write
    #    for the SAME brand-new barcode. --
    def test_concurrent_business_submission_and_upcitemdb_cache_same_barcode(self):
        barcode = f"{4300000000000 + int(self.suffix[:6], 16) % 900000}"[:13]
        errors = []

        def business_worker():
            session = pg_main.SessionLocal()
            try:
                key = pg_main.general_catalog_key_for(barcode, "Business Submitted Name", "500ml")
                pg_main._general_catalog_atomic_get_or_create(
                    session, key=key, barcode=barcode, product_name="Business Submitted Name",
                    brand=None, size="500ml", source="business_submission",
                )
                session.commit()
            except Exception as exc:  # pragma: no cover
                errors.append(exc)
                session.rollback()
            finally:
                session.close()

        def upcitemdb_worker():
            session = pg_main.SessionLocal()
            try:
                pg_main.upsert_general_catalog_identity(
                    session, barcode, "UPCitemdb Confirmed Name", "RealBrand", "500ml", source="upcitemdb",
                )
                session.commit()
            except Exception as exc:  # pragma: no cover
                errors.append(exc)
                session.rollback()
            finally:
                session.close()

        threads = [threading.Thread(target=business_worker), threading.Thread(target=upcitemdb_worker)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(errors, [], f"concurrent business+UPCitemdb catalog writes raised: {errors}")
        rows = self.catalog_rows_for_barcode(barcode)
        self.assertEqual(len(rows), 1, "business submission and UPCitemdb caching must never duplicate a barcode identity")

    # -- Concurrency: same VERIFIED no-barcode identity from two businesses --
    def test_concurrent_same_verified_no_barcode_identity_produces_one_row(self):
        errors = []

        def worker(origin_id):
            session = pg_main.SessionLocal()
            try:
                key = pg_main.general_catalog_key_for(None, "Concurrent Verified Widget", "500ml", uncertain_disambiguator=origin_id)
                pg_main._general_catalog_atomic_get_or_create(
                    session, key=key, barcode=None, product_name="Concurrent Verified Widget",
                    brand=None, size="500ml", source="business_submission",
                )
                session.commit()
            except Exception as exc:  # pragma: no cover
                errors.append(exc)
                session.rollback()
            finally:
                session.close()

        # Same disambiguator on both sides on purpose: with a VERIFIED size,
        # general_catalog_key_for() ignores the disambiguator entirely, so
        # this proves the verified-size path still merges correctly even
        # though two independent "products" are racing.
        threads = [threading.Thread(target=worker, args=(None,)) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(errors, [])
        rows = self.no_barcode_rows_named("Concurrent Verified Widget")
        self.assertEqual(len(rows), 1, "concurrent submissions of the SAME verified no-barcode identity must resolve to one row")

    # -- Concurrency: genuinely DIFFERENT no-barcode identities racing must
    #    never crash and must never accidentally collide into one row. --
    def test_concurrent_conflicting_no_barcode_identities_produce_separate_rows_no_crash(self):
        errors = []

        def worker(i):
            session = pg_main.SessionLocal()
            try:
                name = f"Audit Distinct Product {i}"
                key = pg_main.general_catalog_key_for(None, name, "500ml")
                pg_main._general_catalog_atomic_get_or_create(
                    session, key=key, barcode=None, product_name=name,
                    brand=None, size="500ml", source="business_submission",
                )
                session.commit()
            except Exception as exc:  # pragma: no cover
                errors.append(exc)
                session.rollback()
            finally:
                session.close()

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(errors, [])
        for i in range(5):
            rows = self.no_barcode_rows_named(f"Audit Distinct Product {i}")
            self.assertEqual(len(rows), 1)


if __name__ == "__main__":
    unittest.main()
