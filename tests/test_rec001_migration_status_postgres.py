"""REC-001 (triage 77) against a real PostgreSQL database: migrated to head,
then made to look one migration behind, then missing a mapped column.

Set TEST_POSTGRES_ADMIN_URL (see tests/postgres_test_support.py).
"""
import contextlib
import io
import unittest

from tests.postgres_test_support import ADMIN_URL, create_postgres_test_schema, drop_postgres_test_schema

HEAD = "0042_business_brain_forecast_recompute"
PREFIX = "cauldra_rec001"


@unittest.skipUnless(ADMIN_URL, "TEST_POSTGRES_ADMIN_URL is not configured")
class MigrationStatusPostgresTests(unittest.TestCase):
    """Real database: migrated to head, then made to look one migration behind."""

    @classmethod
    def setUpClass(cls):
        cls.pg = create_postgres_test_schema(PREFIX, {"SUPPLY_AI_AUTO_CREATE_SCHEMA": "false"})
        cls.main = cls.pg.main

    @classmethod
    def tearDownClass(cls):
        drop_postgres_test_schema(cls.pg, PREFIX)

    def _execute(self, statement):
        from sqlalchemy import text
        with self.main.engine.begin() as conn:
            conn.execute(text(statement))

    def test_current_then_behind_then_missing_column_request(self):
        m = self.main
        self.assertEqual(m.read_database_migration_status()["state"], "current")

        self._execute("UPDATE alembic_version SET version_num = '0041_general_catalog_category_retired'")
        status = m.read_database_migration_status()
        self.assertEqual((status["state"], status["pending"]), ("behind", [HEAD]))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            m.log_database_migration_status()
        self.assertIn("DATABASE BEHIND CODE", buf.getvalue())

        # A column the code maps but this database lacks: the ORM read fails.
        self._execute("ALTER TABLE business_profile DROP COLUMN brain_relationships_checked_at")

        def read_business():
            db = m.SessionLocal()
            try:
                return {"count": len(db.query(m.BusinessProfile).limit(1).all())}
            finally:
                db.close()

        m.app.add_api_route("/__rec001_probe/pg", read_business, methods=["GET"])
        from fastapi.testclient import TestClient
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), TestClient(m.app, raise_server_exceptions=False) as client:
            response = client.get("/__rec001_probe/pg")
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json(), {"detail": m.UNHANDLED_ERROR_DETAIL})
        log = buf.getvalue()
        self.assertIn("[schema-behind-code] GET /__rec001_probe/pg", log)
        self.assertIn("brain_relationships_checked_at", log)
        self.assertIn("DATABASE BEHIND CODE", log)

        self._execute("DROP TABLE alembic_version")
        self.assertEqual(m.read_database_migration_status()["state"], "untracked")


if __name__ == "__main__":
    unittest.main()
