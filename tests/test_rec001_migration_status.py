"""REC-001 (triage 77): a database behind the code must be reported plainly.

Startup reads alembic_version (read-only) and logs whether the database matches
the migrations this code ships; a request that fails because a table/column is
missing logs a [schema-behind-code] operator line. Customers still get the
generic ERR-003 message. Nothing here refuses to start (see main.py).

Subprocess-isolated import of `main`, no database needed. The real-database
case is tests/test_rec001_migration_status_postgres.py.
"""
import contextlib
import io
import os
import subprocess
import sys
import textwrap
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HEAD = "0044_subscription_renewal_engine"


class _SchemaError(Exception):
    """Stands in for psycopg's UndefinedColumn: carries a SQLSTATE and diag."""
    def __init__(self, sqlstate, message):
        super().__init__(message)
        self.sqlstate = sqlstate
        self.diag = types.SimpleNamespace(message_primary=message)


def _isolated_main():
    os.environ['PYTHON_DOTENV_DISABLED'] = '1'
    os.environ['SUPPLY_AI_SKIP_DB_STARTUP_CHECK'] = 'true'
    os.environ.setdefault('DATABASE_URL', 'postgresql+psycopg://test:test@127.0.0.1:65432/cauldra_test')
    os.environ.setdefault('SUPPLY_AI_SECRET_KEY', 'isolated-test-secret-012345678901234567890123456789')
    import main
    return main


class MigrationComparisonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.main = _isolated_main()

    def test_code_graph_head_matches_alembic(self):
        graph = self.main.code_migration_graph()
        self.assertIn(HEAD, graph)
        self.assertEqual(self.main.compare_migration_levels([HEAD], graph)["state"], "current")
        # The text parser must agree with Alembic itself (run separately: loading
        # the scripts imports main).
        code = textwrap.dedent("""
            from alembic.config import Config
            from alembic.script import ScriptDirectory
            cfg = Config(); cfg.set_main_option('script_location', 'alembic')
            print(','.join(sorted(ScriptDirectory.from_config(cfg).get_heads())))
        """)
        env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(ROOT / "backend"), str(ROOT)])}
        out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr[-2000:])
        heads = out.stdout.strip().splitlines()[-1].split(',')
        referenced = {d for downs in graph.values() for d in downs}
        self.assertEqual(sorted(r for r in graph if r not in referenced), heads)

    def test_states(self):
        graph = {"a": (), "b": ("a",), "c": ("b",), "d": ("b",), "e": ("c", "d")}  # e merges c and d
        cmp = self.main.compare_migration_levels
        self.assertEqual(cmp(["e"], graph)["state"], "current")
        behind = cmp(["c"], graph)
        self.assertEqual((behind["state"], behind["pending"], behind["code"]), ("behind", ["d", "e"], ["e"]))
        self.assertEqual(cmp(["zz"], graph)["state"], "ahead")
        self.assertEqual(cmp(None, graph)["state"], "untracked")
        self.assertEqual(cmp([], graph)["state"], "empty")
        self.assertEqual(cmp(["a"], {})["state"], "unknown")

    def test_behind_message_names_levels_and_the_fix(self):
        msg = self.main.describe_migration_status(
            self.main.compare_migration_levels(["0041_general_catalog_category_retired"], self.main.code_migration_graph()))
        self.assertIn("DATABASE BEHIND CODE", msg)
        self.assertIn("0041_general_catalog_category_retired", msg)
        self.assertIn(HEAD, msg)
        self.assertIn("alembic upgrade head", msg)
        for state in ("current", "ahead", "untracked", "empty", "unreadable", "unknown"):
            self.assertTrue(self.main.describe_migration_status({"state": state, "code": [HEAD], "database": ["x"]}))


class SchemaMismatchRequestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.main = _isolated_main()
        from sqlalchemy.exc import ProgrammingError

        def missing_column():
            raise ProgrammingError("SELECT 1", {}, _SchemaError("42703", 'column business_profile.new_col does not exist'))

        def other_failure():
            raise RuntimeError("unrelated")

        cls.main.app.add_api_route("/__rec001_probe/missing", missing_column, methods=["GET"])
        cls.main.app.add_api_route("/__rec001_probe/other", other_failure, methods=["GET"])

    def _get(self, path):
        from fastapi.testclient import TestClient
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), TestClient(self.main.app, raise_server_exceptions=False) as client:
            response = client.get(path)
        return response, buf.getvalue()

    def test_missing_column_logs_operator_line_and_keeps_customer_message(self):
        response, log = self._get("/__rec001_probe/missing")
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json(), {"detail": self.main.UNHANDLED_ERROR_DETAIL})
        self.assertNotIn("business_profile", response.text)
        self.assertIn("[schema-behind-code] GET /__rec001_probe/missing", log)
        self.assertIn("missing a column", log)
        self.assertIn("business_profile.new_col", log)

    def test_other_errors_are_not_labelled_schema_problems(self):
        response, log = self._get("/__rec001_probe/other")
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("[schema-behind-code]", log)


if __name__ == "__main__":
    unittest.main()
