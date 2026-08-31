"""Infrastructure regression checks; PostgreSQL integration is opt-in by URL."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

from postgres_test_support import ADMIN_URL, create_postgres_test_schema, drop_postgres_test_schema

ROOT = Path(__file__).resolve().parents[1]
SECRET = "test-secret-0123456789abcdef0123456789abcdef0123456789abcdef"


def _with_backend_on_path(env):
    """backend/ (where main.py now lives) must be importable in child
    processes that do a bare `import main` — pytest's own process gets this
    from the root conftest, subprocesses started here do not."""
    backend = str(ROOT / "backend")
    existing = env.get("PYTHONPATH", "")
    return {**env, "PYTHONPATH": os.pathsep.join(p for p in (backend, existing) if p)}


def run(command, env):
    return subprocess.run(command, cwd=ROOT, env=_with_backend_on_path(env), text=True, capture_output=True, check=False)


class InfrastructureTests(unittest.TestCase):
    def test_environment_example_never_contains_an_active_database_url(self):
        example_lines = (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
        active_database_urls = [line for line in example_lines if line.strip().startswith("DATABASE_URL=")]
        self.assertEqual(active_database_urls, [], "DATABASE_URL must be injected securely, never copied into .env.example")
        placeholders = [line for line in example_lines if line.strip().startswith("# DATABASE_URL=")]
        self.assertEqual(len(placeholders), 1)
        self.assertIn("postgresql+psycopg://", placeholders[0])
        self.assertIn("replace-me", placeholders[0])

    def test_application_container_does_not_run_migrations_on_startup(self):
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        command = next(line for line in dockerfile.splitlines() if line.startswith("CMD "))
        self.assertIn("uvicorn main:app", command)
        self.assertNotIn("alembic upgrade", command)

    def test_production_rejects_sqlite(self):
        env = os.environ | {"SUPPLY_AI_ENV": "production", "DATABASE_URL": "sqlite:///blocked.db", "SUPPLY_AI_SECRET_KEY": SECRET, "SUPPLY_AI_TRUSTED_HOSTS": "example.test", "SUPPLY_AI_REFRESH_COOKIE_SECURE": "true"}
        result = run([sys.executable, "-c", "import main"], env)
        self.assertNotEqual(result.returncode, 0)
        # Current, environment-agnostic wording — DATABASE_URL is now rejected
        # in every environment when it isn't PostgreSQL, not just production
        # (see main.py's DATABASE_URL validation), so the message no longer
        # says "Production requires...".
        self.assertIn("DATABASE_URL must be a PostgreSQL connection string", result.stderr)

    def test_production_postgres_url_selects_pinned_psycopg_driver(self):
        env = os.environ | {
            "SUPPLY_AI_ENV": "production",
            "DATABASE_URL": "postgresql+psycopg://user:password@127.0.0.1:5432/cauldra",
            "SUPPLY_AI_SECRET_KEY": SECRET,
            "SUPPLY_AI_TRUSTED_HOSTS": "example.test",
            "SUPPLY_AI_REFRESH_COOKIE_SECURE": "true",
            # This test verifies URL normalization/driver selection, not real
            # connectivity — 127.0.0.1:5432 is intentionally unreachable, so
            # the real startup connectivity check (added after this test was
            # first written) must be bypassed or it would try to connect for
            # real and time out instead of exercising what this test is for.
            "SUPPLY_AI_SKIP_DB_STARTUP_CHECK": "true",
        }
        result = run([sys.executable, "-c", "import main; print(main.engine.dialect.driver)"], env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "psycopg")

    def test_local_storage_blocks_traversal_and_round_trips_private_bytes(self):
        from storage import LocalStorage
        with tempfile.TemporaryDirectory() as temp:
            storage = LocalStorage(Path(temp))
            storage.put_bytes("1/private.txt", b"private", "text/plain")
            self.assertEqual(storage.read_bytes("1/private.txt"), b"private")
            with self.assertRaises(ValueError):
                storage.read_bytes("../outside.txt")

    @unittest.skipUnless(os.getenv("TEST_POSTGRES_URL"), "TEST_POSTGRES_URL is not configured")
    def test_postgres_migration_connectivity(self):
        env = os.environ | {"DATABASE_URL": os.environ["TEST_POSTGRES_URL"], "SUPPLY_AI_SECRET_KEY": SECRET, "SUPPLY_AI_ENV": "development"}
        result = run([sys.executable, "-m", "alembic", "upgrade", "head"], env)
        self.assertEqual(result.returncode, 0, result.stderr)


# -----------------------------------------------------------------------------
# The tests below need a real, live application bound to PostgreSQL — they
# share ONE schema for the whole class (see tests/postgres_test_support.py,
# the same create-schema/alembic-upgrade/drop-schema pattern
# tests/test_mutation_idempotency_postgres.py already uses). Each test's own
# setUp()/body creates brand-new, uniquely-named businesses/users, so they
# never interfere with each other despite sharing one schema.
# -----------------------------------------------------------------------------
@unittest.skipUnless(ADMIN_URL, "TEST_POSTGRES_ADMIN_URL is not configured")
class PostgresBackedInfrastructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctx = create_postgres_test_schema("cauldra_infra")
        cls.main = cls.ctx.main

    @classmethod
    def tearDownClass(cls):
        drop_postgres_test_schema(cls.ctx, "cauldra_infra")

    def test_alembic_creates_a_working_fresh_postgresql_schema(self):
        """Direct PostgreSQL equivalent of the old SQLite
        test_alembic_creates_fresh_sqlite_schema_for_local_verification:
        `alembic upgrade head` against a brand-new, empty schema must produce
        a working Cauldra schema (application tables + alembic's own version
        table).

        Deliberately does NOT go through create_postgres_test_schema()'s
        `importlib.import_module("main")` step: `main` is already imported
        and cached by this class's setUpClass, and a second
        import_module("main") call would just return that SAME cached
        module (bound to the CLASS's schema, via PGOPTIONS at the time it
        was first imported) rather than a fresh one bound to the schema
        created here — Python does not re-run a module's top-level code on a
        second import. Inspecting via a raw engine scoped to this schema
        with its own connect_args avoids that entirely and is a more
        precise test of the actual thing being verified: what `alembic
        upgrade head` produces in a truly empty schema, independent of
        whatever this process already has imported."""
        import re
        from sqlalchemy import create_engine, inspect as sa_inspect

        schema = f"cauldra_freshcheck_{uuid.uuid4().hex[:12]}"
        assert re.fullmatch(r"cauldra_freshcheck_[a-f0-9]{12}", schema)
        admin_engine = create_engine(ADMIN_URL, pool_pre_ping=True)
        try:
            with admin_engine.begin() as connection:
                connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')

            env = os.environ | {
                "DATABASE_URL": ADMIN_URL, "PGOPTIONS": f"-csearch_path={schema}",
                "SUPPLY_AI_ENV": "development", "SUPPLY_AI_SECRET_KEY": SECRET,
            }
            migration = run([sys.executable, "-m", "alembic", "upgrade", "head"], env)
            self.assertEqual(migration.returncode, 0, migration.stderr)

            scoped_engine = create_engine(ADMIN_URL, pool_pre_ping=True, connect_args={"options": f"-csearch_path={schema}"})
            try:
                names = sa_inspect(scoped_engine).get_table_names()
            finally:
                scoped_engine.dispose()
            self.assertIn("business_profile", names)
            self.assertIn("alembic_version", names)
            self.assertIn("notifications", names)  # a table added well after the baseline migration
        finally:
            with admin_engine.begin() as connection:
                connection.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            admin_engine.dispose()

    def test_product_query_is_business_scoped(self):
        main = self.main
        db = main.SessionLocal()
        try:
            suffix = uuid.uuid4().hex[:10]
            a = main.BusinessProfile(business_code=f"TENANT-A-{suffix}", company_name="A")
            b = main.BusinessProfile(business_code=f"TENANT-B-{suffix}", company_name="B")
            db.add_all([a, b])
            db.flush()
            ua = main.User(username=f"tenant-a-{suffix}", password="x", role="admin", email=f"a-{suffix}@a.test", phone="1", business_id=a.id)
            db.add_all([ua, main.Product(sku=f"A1-{suffix}", name="A product", category="x", business_id=a.id),
                        main.Product(sku=f"B1-{suffix}", name="B product", category="x", business_id=b.id)])
            db.commit()
            rows = main.list_products(limit=200, offset=0, warehouse=None, stock_status=None, user=ua, db=db)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["sku"], f"A1-{suffix}")
            b_product = db.query(main.Product).filter(main.Product.business_id == b.id).first()
            self.assertIsNone(db.query(main.Product).filter(main.Product.id == b_product.id, main.Product.business_id == a.id).first())
        finally:
            db.close()

    def test_manager_and_staff_can_sign_in_after_logout_from_fresh_clients(self):
        from fastapi.testclient import TestClient

        main = self.main
        suffix = uuid.uuid4().hex[:10]
        db = main.SessionLocal()
        business = main.BusinessProfile(business_code=f"TEAM-{suffix}", company_name="Team Test")
        db.add(business)
        db.flush()
        for role in ("manager", "staff"):
            db.add(main.User(
                username=f"{role.title()} User {suffix}",
                password=main.hash_password(f"{role.title()}Pass9"),
                role=role, firstname=role.title(), lastname="User",
                email=f"{role}-{suffix}@team.test", phone="08000000000",
                position=role.title(), business_id=business.id, disabled=False,
                must_change_password=True, auth_version=1,
            ))
        db.commit()
        business_code = business.business_code
        db.close()

        for role in ("manager", "staff"):
            credentials = {
                "business_id": business_code.lower(),
                "username": f"  {role.title()} User {suffix}  ",
                "password": f"{role.title()}Pass9",
                "selected_role": role,
            }
            first_client = TestClient(main.app)
            first_login = first_client.post("/auth/employee-login", json=credentials)
            self.assertEqual(first_login.status_code, 200, first_login.text)
            first_token = first_login.json()["access_token"]
            logout = first_client.post("/auth/logout", headers={"Authorization": f"Bearer {first_token}"})
            self.assertEqual(logout.status_code, 200, logout.text)

            fresh_client = TestClient(main.app)
            fresh_login = fresh_client.post("/auth/employee-login", json=credentials)
            self.assertEqual(fresh_login.status_code, 200, fresh_login.text)
            self.assertEqual(fresh_login.json()["role"], role)

    def test_registered_admin_can_log_in_after_logout_with_fresh_client_and_restart(self):
        from datetime import datetime, timedelta
        from fastapi.testclient import TestClient

        main = self.main
        suffix = uuid.uuid4().hex[:10]
        main.PLAN_CONFIG["starter"]["paystack_monthly_plan_code"] = None
        main.PLAN_CONFIG["starter"]["paystack_annual_plan_code"] = None

        registration = {
            "company_name": "Registered Admin Test",
            "email": f"business-{suffix}@registered-admin.example.com",
            "phone": "+2348012345678",
            "address": "1 Test Street",
            "firstname": "Registered",
            "lastname": "Admin",
            "owner_email": f"owner-{suffix}@registered-admin.example.com",
            "owner_phone": "+2348012345678",
            "username": f"Registered Admin {suffix}",
            "password": "RegisteredAdmin9",
            "position": "Admin",
            "country": "Nigeria",
            "country_code": "NG",
            "language": "en",
            "payment_reference": f"registered-admin-payment-{suffix}",
        }

        db = main.SessionLocal()
        db.add(main.OnboardingAuthorization(
            paystack_reference=registration["payment_reference"],
            email=registration["owner_email"],
            plan="starter", billing_interval="monthly", amount_kobo=5000,
            status="verified",
            paystack_customer_code=f"CUS_{suffix}",
            paystack_authorization_code=f"AUTH_{suffix}",
            verified_at=datetime.utcnow(),
            expires_at=datetime.utcnow() + timedelta(hours=1),
        ))
        db.commit()
        db.close()

        registration_client = TestClient(main.app)
        registered = registration_client.post("/auth/register-business", json=registration)
        self.assertEqual(registered.status_code, 200, registered.text)
        registered_data = registered.json()
        business_code = registered_data["business_code"]
        access_token = registered_data["access_token"]
        self.assertEqual(registered_data["role"], "admin")
        self.assertEqual(registered_data["username"], registration["username"])

        auto_login_me = registration_client.get("/auth/me", headers={"Authorization": f"Bearer {access_token}"})
        self.assertEqual(auto_login_me.status_code, 200, auto_login_me.text)
        self.assertEqual(auto_login_me.json()["business_code"], business_code)
        self.assertEqual(auto_login_me.json()["role"], "admin")

        logout = registration_client.post("/auth/logout", headers={"Authorization": f"Bearer {access_token}"})
        self.assertEqual(logout.status_code, 200, logout.text)
        revoked = registration_client.get("/auth/me", headers={"Authorization": f"Bearer {access_token}"})
        self.assertEqual(revoked.status_code, 401, revoked.text)

        db = main.SessionLocal()
        business = main.get_business_by_code(db, business_code)
        admin = db.query(main.User).filter(main.User.business_id == business.id, main.User.role == "admin").one()
        self.assertEqual(admin.username, registration["username"])
        self.assertFalse(admin.disabled)
        self.assertTrue(main.verify_password(registration["password"], admin.password))
        db.close()

        fresh_client = TestClient(main.app)
        fresh_login = fresh_client.post("/auth/admin-login", json={
            "business_id": business_code, "username": registration["username"], "password": registration["password"],
        })
        self.assertEqual(fresh_login.status_code, 200, fresh_login.text)
        self.assertEqual(fresh_login.json()["business_code"], business_code)
        self.assertEqual(fresh_login.json()["role"], "admin")

        # "Restart" — a brand-new process re-imports main against the SAME
        # already-migrated schema and must authenticate the same admin.
        restart_env = os.environ.copy()
        restart_env.update({
            "CAULDRA_TEST_BUSINESS_CODE": business_code,
            "CAULDRA_TEST_USERNAME": registration["username"],
            "CAULDRA_TEST_PASSWORD": registration["password"],
        })
        restart_script = """
import os
import main
from fastapi.testclient import TestClient

main.PLAN_CONFIG['starter']['paystack_monthly_plan_code'] = None
main.PLAN_CONFIG['starter']['paystack_annual_plan_code'] = None
client = TestClient(main.app)
response = client.post('/auth/admin-login', json={
    'business_id': os.environ['CAULDRA_TEST_BUSINESS_CODE'],
    'username': os.environ['CAULDRA_TEST_USERNAME'],
    'password': os.environ['CAULDRA_TEST_PASSWORD'],
})
assert response.status_code == 200, response.text
db = main.SessionLocal()
business = main.get_business_by_code(db, os.environ['CAULDRA_TEST_BUSINESS_CODE'])
admin = db.query(main.User).filter(main.User.business_id == business.id, main.User.role == 'admin').one()
assert main.verify_password(os.environ['CAULDRA_TEST_PASSWORD'], admin.password)
db.close()
"""
        restarted = subprocess.run([sys.executable, "-c", restart_script], cwd=ROOT, env=_with_backend_on_path(restart_env), text=True, capture_output=True, check=False)
        self.assertEqual(restarted.returncode, 0, restarted.stderr)

    # --- /health (section: GET /health performs a real PostgreSQL check) ---

    def test_health_returns_ok_when_database_reachable(self):
        from fastapi.testclient import TestClient

        r = TestClient(self.main.app).get("/health")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["database"], "ok")
        self.assertIn("version", body)
        self.assertIn("refresh_enabled", body)

    def test_health_response_never_contains_credentials(self):
        from fastapi.testclient import TestClient

        r = TestClient(self.main.app).get("/health")
        password = self.main.engine.url.password or ""
        if password:
            self.assertNotIn(password, r.text)
        self.assertNotIn(ADMIN_URL, r.text)
        self.assertNotIn("DATABASE_URL", r.text)

    def test_health_releases_its_connection_back_to_the_pool(self):
        from fastapi.testclient import TestClient

        client = TestClient(self.main.app)
        self.assertEqual(self.main.engine.pool.checkedout(), 0)
        for _ in range(5):
            r = client.get("/health")
            self.assertEqual(r.status_code, 200)
        self.assertEqual(self.main.engine.pool.checkedout(), 0, "GET /health must always release its connection, never leak one")


@unittest.skipUnless(ADMIN_URL, "TEST_POSTGRES_ADMIN_URL is not configured")
class HealthCheckUnavailableDatabaseTests(unittest.TestCase):
    """Run as an isolated subprocess (not the shared class above) because
    this specifically needs an app bound to an UNREACHABLE database — the
    real startup connectivity check must be bypassed with
    SUPPLY_AI_SKIP_DB_STARTUP_CHECK=true (a test-only escape hatch; the real
    production startup check is never disabled) so the app still imports,
    and then GET /health's own SELECT 1 is what's expected to fail."""

    def test_health_returns_503_when_database_unavailable(self):
        env = os.environ | {
            "DATABASE_URL": "postgresql+psycopg://baduser:badpass@127.0.0.1:5432/nonexistent",
            "SUPPLY_AI_SECRET_KEY": SECRET,
            "SUPPLY_AI_ENV": "development",
            "SUPPLY_AI_SKIP_DB_STARTUP_CHECK": "true",
            "DATABASE_CONNECT_TIMEOUT": "2",
        }
        script = (
            "import main, json\n"
            "from fastapi.testclient import TestClient\n"
            "r = TestClient(main.app).get('/health')\n"
            "print(json.dumps({'status_code': r.status_code, 'body': r.json(), 'text': r.text}))\n"
        )
        result = run([sys.executable, "-c", script], env)
        self.assertEqual(result.returncode, 0, result.stderr)
        import json as _json
        payload = _json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual(payload["status_code"], 503)
        self.assertEqual(payload["body"], {"status": "degraded", "database": "unavailable", "version": payload["body"]["version"], "refresh_enabled": True})
        self.assertNotIn("badpass", payload["text"])
        self.assertNotIn("baduser", payload["text"])


if __name__ == "__main__":
    unittest.main()
