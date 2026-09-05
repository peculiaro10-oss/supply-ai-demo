"""Tests for scripts/backup_database.py.

Everything here is mocked: no real pg_dump binary, no real database, no real
Cloudflare R2/boto3 network call is ever made. os.environ is snapshotted and
restored around every test so nothing here can read (or leak) the real
production DATABASE_URL / R2 credentials from a developer's actual .env —
note the script itself only calls load_dotenv() under `if __name__ ==
"__main__"`, never at import time, specifically so importing it for these
tests can never pull the real .env into os.environ.
"""
from __future__ import annotations

import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import backup_database as bd  # noqa: E402

FAKE_DATABASE_URL = "postgresql+psycopg://cauldra_user:S3cretPassw0rd!@aws-1-eu-west-1.pooler.supabase.com:5432/postgres?sslmode=require"
FAKE_R2 = {
    "R2_ACCESS_KEY_ID": "fake-r2-access-key-id",
    "R2_SECRET_ACCESS_KEY": "fake-r2-secret-access-key-do-not-log",
    "R2_ENDPOINT": "https://fake-account-id.r2.cloudflarestorage.com",
    "R2_BACKUP_BUCKET": "cauldra-backups",
}


class EnvIsolatedTestCase(unittest.TestCase):
    """Every test starts from a clean, fully-controlled environment — the
    required vars are never inherited from the real process environment."""

    def setUp(self):
        self._original_environ = os.environ.copy()
        for name in bd.REQUIRED_ENV_VARS:
            os.environ.pop(name, None)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._original_environ)

    def set_full_valid_env(self):
        os.environ["DATABASE_URL"] = FAKE_DATABASE_URL
        os.environ.update(FAKE_R2)


class ConfigLoadingTests(EnvIsolatedTestCase):
    def test_missing_all_config_lists_every_missing_variable(self):
        with self.assertRaises(bd.BackupError) as ctx:
            bd.load_config()
        for name in bd.REQUIRED_ENV_VARS:
            self.assertIn(name, str(ctx.exception))

    def test_missing_r2_configuration_only(self):
        os.environ["DATABASE_URL"] = FAKE_DATABASE_URL
        with self.assertRaises(bd.BackupError) as ctx:
            bd.load_config()
        message = str(ctx.exception)
        self.assertIn("R2_ACCESS_KEY_ID", message)
        self.assertIn("R2_SECRET_ACCESS_KEY", message)
        self.assertIn("R2_ENDPOINT", message)
        self.assertIn("R2_BACKUP_BUCKET", message)
        self.assertNotIn("DATABASE_URL", message)

    def test_full_valid_config_loads(self):
        self.set_full_valid_env()
        config = bd.load_config()
        self.assertEqual(config["DATABASE_URL"], FAKE_DATABASE_URL)
        self.assertEqual(config["R2_BACKUP_BUCKET"], "cauldra-backups")


class DatabaseUrlParsingTests(unittest.TestCase):
    def test_parses_psycopg_driver_suffix_and_sslmode(self):
        conn = bd.parse_database_url(FAKE_DATABASE_URL)
        self.assertEqual(conn["host"], "aws-1-eu-west-1.pooler.supabase.com")
        self.assertEqual(conn["port"], "5432")
        self.assertEqual(conn["user"], "cauldra_user")
        self.assertEqual(conn["password"], "S3cretPassw0rd!")
        self.assertEqual(conn["dbname"], "postgres")
        self.assertEqual(conn["sslmode"], "require")

    def test_parses_plain_postgres_scheme_without_sslmode(self):
        conn = bd.parse_database_url("postgres://u:p@localhost:5433/mydb")
        self.assertEqual(conn["host"], "localhost")
        self.assertEqual(conn["port"], "5433")
        self.assertIsNone(conn["sslmode"])

    def test_rejects_non_postgres_url(self):
        with self.assertRaises(bd.BackupError):
            bd.parse_database_url("mysql://u:p@host/db")

    def test_rejects_url_without_dbname(self):
        with self.assertRaises(bd.BackupError):
            bd.parse_database_url("postgresql://u:p@host:5432/")


class ObjectPathGenerationTests(unittest.TestCase):
    def test_backup_filename_format(self):
        now = datetime(2026, 9, 5, 10, 30, 0, tzinfo=timezone.utc)
        self.assertEqual(bd.backup_filename(now), "cauldra-db-2026-09-05T10-30-00Z.dump")

    def test_object_key_uses_year_month_subfolders_not_bucket_root(self):
        now = datetime(2026, 9, 5, 10, 30, 0, tzinfo=timezone.utc)
        filename = bd.backup_filename(now)
        key = bd.object_key_for(filename, now)
        self.assertEqual(key, "database/2026/09/cauldra-db-2026-09-05T10-30-00Z.dump")
        self.assertFalse(key.startswith("cauldra-db-"), "must not be placed at the bucket root")

    def test_object_key_pads_single_digit_month(self):
        now = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        key = bd.object_key_for(bd.backup_filename(now), now)
        self.assertIn("/2026/01/", key)


class ScrubSecretsTests(unittest.TestCase):
    def test_redacts_connection_strings(self):
        text = "connection to server failed: postgresql://user:hunter2@db.example.com:5432/postgres timed out"
        scrubbed = bd.scrub_secrets(text)
        self.assertNotIn("hunter2", scrubbed)
        self.assertNotIn("db.example.com", scrubbed)
        self.assertIn("[REDACTED CONNECTION STRING]", scrubbed)

    def test_passes_through_ordinary_text(self):
        self.assertEqual(bd.scrub_secrets("pg_dump: error: no matching tables"), "pg_dump: error: no matching tables")

    def test_handles_none(self):
        self.assertEqual(bd.scrub_secrets(None), "")


class RunPgDumpTests(EnvIsolatedTestCase):
    def _conn(self):
        return bd.parse_database_url(FAKE_DATABASE_URL)

    def test_pg_dump_binary_missing_raises_backup_error(self):
        with patch("backup_database.subprocess.run", side_effect=FileNotFoundError()):
            with self.assertRaises(bd.BackupError) as ctx:
                bd.run_pg_dump(self._conn(), Path("unused.dump"))
        self.assertIn("pg_dump", str(ctx.exception))

    def test_pg_dump_nonzero_exit_raises_and_scrubs_stderr(self):
        fake_result = MagicMock(returncode=1, stderr=f"could not connect: {FAKE_DATABASE_URL}")
        with patch("backup_database.subprocess.run", return_value=fake_result):
            with self.assertRaises(bd.BackupError) as ctx:
                bd.run_pg_dump(self._conn(), Path("unused.dump"))
        message = str(ctx.exception)
        self.assertIn("pg_dump exited with status 1", message)
        self.assertNotIn("S3cretPassw0rd", message)

    def test_pg_dump_success_but_empty_file_is_a_failure(self):
        tmp_path = Path("empty_test.dump")
        tmp_path.write_bytes(b"")
        try:
            fake_result = MagicMock(returncode=0, stderr="")
            with patch("backup_database.subprocess.run", return_value=fake_result):
                with self.assertRaises(bd.BackupError):
                    bd.run_pg_dump(self._conn(), tmp_path)
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_pg_dump_success_with_nonempty_file_passes(self):
        tmp_path = Path("ok_test.dump")
        tmp_path.write_bytes(b"PGDMP-fake-dump-bytes")
        try:
            fake_result = MagicMock(returncode=0, stderr="")
            with patch("backup_database.subprocess.run", return_value=fake_result) as run_mock:
                bd.run_pg_dump(self._conn(), tmp_path)
            passed_env = run_mock.call_args.kwargs["env"]
            self.assertEqual(passed_env["PGPASSWORD"], "S3cretPassw0rd!")
            self.assertEqual(passed_env["PGSSLMODE"], "require")
            passed_argv = run_mock.call_args.args[0]
            self.assertNotIn("S3cretPassw0rd!", passed_argv, "password must never be passed as a CLI argument")
        finally:
            tmp_path.unlink(missing_ok=True)


class MainOrchestrationTests(EnvIsolatedTestCase):
    """Full main() runs with run_pg_dump/build_r2_client mocked out — no
    real subprocess, no real network call."""

    def _make_tmp_writer(self, content: bytes):
        def _fake_run_pg_dump(conn, output_path):
            output_path.write_bytes(content)
        return _fake_run_pg_dump

    def test_missing_r2_configuration_exits_nonzero_without_touching_dump_or_upload(self):
        os.environ["DATABASE_URL"] = FAKE_DATABASE_URL
        with patch("backup_database.run_pg_dump") as run_dump, patch("backup_database.build_r2_client") as build_client:
            buf = io.StringIO()
            with redirect_stdout(buf):
                exit_code = bd.main()
        self.assertEqual(exit_code, 1)
        run_dump.assert_not_called()
        build_client.assert_not_called()
        self.assertIn("Backup failed", buf.getvalue())
        self.assertNotIn("completed successfully", buf.getvalue())

    def test_failed_pg_dump_exits_nonzero_and_never_attempts_upload(self):
        self.set_full_valid_env_helper()
        with patch("backup_database.run_pg_dump", side_effect=bd.BackupError("pg_dump exited with status 1: [REDACTED CONNECTION STRING]")), \
             patch("backup_database.build_r2_client") as build_client:
            buf = io.StringIO()
            with redirect_stdout(buf):
                exit_code = bd.main()
        self.assertEqual(exit_code, 1)
        build_client.assert_not_called()
        output = buf.getvalue()
        self.assertIn("Backup failed", output)
        self.assertNotIn("uploaded successfully", output)

    def test_failed_r2_upload_exits_nonzero_and_removes_temp_file(self):
        self.set_full_valid_env_helper()
        fake_client = MagicMock()
        fake_client.upload_file.side_effect = RuntimeError("network unreachable")
        captured_tmp_paths = []

        original_new_temp_path = bd.new_temp_path

        def _tracking_new_temp_path():
            path = original_new_temp_path()
            captured_tmp_paths.append(path)
            return path

        with patch("backup_database.new_temp_path", side_effect=_tracking_new_temp_path), \
             patch("backup_database.run_pg_dump", side_effect=self._make_tmp_writer(b"fake-dump-bytes")), \
             patch("backup_database.build_r2_client", return_value=fake_client):
            buf = io.StringIO()
            with redirect_stdout(buf):
                exit_code = bd.main()

        self.assertEqual(exit_code, 1)
        self.assertEqual(len(captured_tmp_paths), 1)
        self.assertFalse(captured_tmp_paths[0].exists(), "temporary dump file must be removed even after a failed upload")
        output = buf.getvalue()
        self.assertIn("Temporary backup removed.", output)
        self.assertNotIn("completed successfully", output)

    def test_upload_verification_failure_does_not_declare_success(self):
        self.set_full_valid_env_helper()
        fake_client = MagicMock()
        fake_client.upload_file.return_value = None
        fake_client.head_object.return_value = {"ContentLength": 1}  # wrong size on purpose

        with patch("backup_database.run_pg_dump", side_effect=self._make_tmp_writer(b"fake-dump-bytes-longer")), \
             patch("backup_database.build_r2_client", return_value=fake_client):
            buf = io.StringIO()
            with redirect_stdout(buf):
                exit_code = bd.main()

        self.assertEqual(exit_code, 1)
        output = buf.getvalue()
        self.assertNotIn("completed successfully", output)
        self.assertIn("Backup failed", output)

    def test_successful_backup_full_flow_and_secret_safe_logging(self):
        self.set_full_valid_env_helper()
        fake_client = MagicMock()
        fake_client.upload_file.return_value = None
        dump_bytes = b"fake-dump-bytes-for-success-path"
        fake_client.head_object.return_value = {"ContentLength": len(dump_bytes)}

        captured_tmp_paths = []
        original_new_temp_path = bd.new_temp_path

        def _tracking_new_temp_path():
            path = original_new_temp_path()
            captured_tmp_paths.append(path)
            return path

        with patch("backup_database.new_temp_path", side_effect=_tracking_new_temp_path), \
             patch("backup_database.run_pg_dump", side_effect=self._make_tmp_writer(dump_bytes)), \
             patch("backup_database.build_r2_client", return_value=fake_client) as build_client:
            buf = io.StringIO()
            with redirect_stdout(buf):
                exit_code = bd.main()

        self.assertEqual(exit_code, 0)
        output = buf.getvalue()

        # Exact required safe log lines, in order.
        for expected_line in (
            "Starting Cauldra database backup...",
            "Database dump created successfully.",
            "Uploading backup to R2...",
            "Temporary backup removed.",
            "Backup completed successfully.",
        ):
            self.assertIn(expected_line, output)
        self.assertRegex(output, r"Backup uploaded successfully: database/\d{4}/\d{2}/cauldra-db-.*\.dump")

        # Never log secrets.
        self.assertNotIn("S3cretPassw0rd!", output)
        self.assertNotIn(FAKE_R2["R2_SECRET_ACCESS_KEY"], output)
        self.assertNotIn(FAKE_R2["R2_ACCESS_KEY_ID"], output)
        self.assertNotIn(FAKE_DATABASE_URL, output)

        # build_r2_client received credentials via arguments, not globals/env re-reads inside main's log path.
        build_client.assert_called_once_with(FAKE_R2["R2_ACCESS_KEY_ID"], FAKE_R2["R2_SECRET_ACCESS_KEY"], FAKE_R2["R2_ENDPOINT"])

        # Temp file cleaned up.
        self.assertEqual(len(captured_tmp_paths), 1)
        self.assertFalse(captured_tmp_paths[0].exists())

    def set_full_valid_env_helper(self):
        os.environ["DATABASE_URL"] = FAKE_DATABASE_URL
        os.environ.update(FAKE_R2)


if __name__ == "__main__":
    unittest.main()
