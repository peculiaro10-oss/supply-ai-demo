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


if __name__ == "__main__":
    unittest.main()
