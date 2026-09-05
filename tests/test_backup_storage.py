"""Tests for scripts/backup_storage.py.

Everything here is mocked: no real Supabase Storage call, no real Cloudflare
R2/boto3 network call. os.environ is snapshotted and restored around every
test. FakeSupabaseBucket and FakeR2Client below are small, precise stand-ins
for the exact bits of the storage3 / boto3 APIs this script actually uses.
"""
from __future__ import annotations

import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any, Dict, Optional
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import backup_storage as bs  # noqa: E402

FAKE_R2 = {
    "R2_ACCESS_KEY_ID": "fake-r2-access-key-id",
    "R2_SECRET_ACCESS_KEY": "fake-r2-secret-access-key-do-not-log",
    "R2_ENDPOINT": "https://fake-account-id.r2.cloudflarestorage.com",
    "R2_BACKUP_BUCKET": "cauldra-backups",
}
FAKE_SUPABASE_SECRET = "sb_secret_fake0123456789doNotLogThisEver"


class FakeSupabaseBucket:
    """In-memory stand-in for storage3's SyncBucketProxy. `tree` is a nested
    dict: {"a.txt": b"bytes", "folder": {"b.txt": b"bytes", ...}}. Mimics
    .list() (one folder level per call, paginated, id=None for folder
    placeholders — the real Supabase Storage convention) and .download()
    (returns bytes) closely enough to exercise iter_all_objects() and
    backup_one_object() without any network call."""

    def __init__(self, tree: Optional[Dict[str, Any]] = None):
        self.tree = tree if tree is not None else {}
        self.list_calls = []
        self.download_calls = []
        self.list_exception: Optional[Exception] = None
        self.download_exceptions: Dict[str, Exception] = {}

    def _node_at(self, prefix: str):
        node = self.tree
        cleaned = prefix.strip("/")
        if cleaned:
            for part in cleaned.split("/"):
                if not isinstance(node, dict):
                    return {}
                node = node.get(part, {})
        return node

    def list(self, path=None, options=None):
        prefix = path or ""
        self.list_calls.append((prefix, dict(options or {})))
        if self.list_exception is not None:
            raise self.list_exception
        node = self._node_at(prefix)
        if not isinstance(node, dict):
            return []
        names = sorted(node.keys())
        limit = (options or {}).get("limit", bs.LIST_PAGE_SIZE)
        offset = (options or {}).get("offset", 0)
        page_names = names[offset: offset + limit]
        entries = []
        for name in page_names:
            value = node[name]
            if isinstance(value, dict):
                entries.append({"name": name, "id": None, "metadata": None})
            else:
                entries.append({"name": name, "id": f"id-{name}", "metadata": {"size": len(value)}})
        return entries

    def download(self, path: str):
        self.download_calls.append(path)
        if path in self.download_exceptions:
            raise self.download_exceptions[path]
        node = self.tree
        parts = path.split("/")
        for part in parts[:-1]:
            node = node.get(part, {}) if isinstance(node, dict) else {}
        value = node.get(parts[-1]) if isinstance(node, dict) else None
        if value is None or isinstance(value, dict):
            raise RuntimeError(f"object not found: {path}")
        return value


class FakeR2Client:
    """In-memory stand-in for the boto3 S3 client methods this script uses."""

    def __init__(self):
        self.objects: Dict[str, bytes] = {}
        self.put_exceptions: Dict[str, Exception] = {}
        self.head_size_overrides: Dict[str, int] = {}

    def put_object(self, Bucket, Key, Body):
        if Key in self.put_exceptions:
            raise self.put_exceptions[Key]
        self.objects[Key] = bytes(Body)

    def head_object(self, Bucket, Key):
        if Key in self.head_size_overrides:
            return {"ContentLength": self.head_size_overrides[Key]}
        if Key not in self.objects:
            raise RuntimeError("NoSuchKey")
        return {"ContentLength": len(self.objects[Key])}


class EnvIsolatedTestCase(unittest.TestCase):
    def setUp(self):
        self._original_environ = os.environ.copy()
        for name in bs.REQUIRED_R2_ENV_VARS:
            os.environ.pop(name, None)
        bs._KNOWN_SECRETS.clear()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._original_environ)
        bs._KNOWN_SECRETS.clear()

    def set_full_valid_r2_env(self):
        os.environ.update(FAKE_R2)


class R2ConfigTests(EnvIsolatedTestCase):
    def test_missing_all_r2_configuration_lists_every_missing_variable(self):
        with self.assertRaises(bs.BackupError) as ctx:
            bs.load_r2_config()
        for name in bs.REQUIRED_R2_ENV_VARS:
            self.assertIn(name, str(ctx.exception))

    def test_missing_one_r2_variable(self):
        self.set_full_valid_r2_env()
        os.environ.pop("R2_ENDPOINT")
        with self.assertRaises(bs.BackupError) as ctx:
            bs.load_r2_config()
        self.assertIn("R2_ENDPOINT", str(ctx.exception))

    def test_valid_r2_config_loads_and_registers_secrets_for_scrubbing(self):
        self.set_full_valid_r2_env()
        config = bs.load_r2_config()
        self.assertEqual(config["R2_BACKUP_BUCKET"], "cauldra-backups")
        leaked = bs.scrub_secrets(f"error using key {FAKE_R2['R2_SECRET_ACCESS_KEY']} and {FAKE_R2['R2_ACCESS_KEY_ID']}")
        self.assertNotIn(FAKE_R2["R2_SECRET_ACCESS_KEY"], leaked)
        self.assertNotIn(FAKE_R2["R2_ACCESS_KEY_ID"], leaked)


class SecretScrubbingTests(unittest.TestCase):
    def tearDown(self):
        bs._KNOWN_SECRETS.clear()

    def test_redacts_registered_secret_verbatim(self):
        bs._register_secret("super-secret-value-123")
        self.assertNotIn("super-secret-value-123", bs.scrub_secrets("failed with token super-secret-value-123 in header"))

    def test_redacts_supabase_jwt_pattern(self):
        fake_jwt = "eyJhbGciOiJIUzI1NiJ9.eyJyb2xlIjoic2VydmljZV9yb2xlIn0.abcdefghijklmnopqrstuvwxyz1234567890"
        scrubbed = bs.scrub_secrets(f"Authorization: Bearer {fake_jwt}")
        self.assertNotIn(fake_jwt, scrubbed)
        self.assertIn("[REDACTED]", scrubbed)

    def test_redacts_sb_secret_key_pattern(self):
        scrubbed = bs.scrub_secrets("using key sb_secret_abcXYZ123_doNotLog")
        self.assertNotIn("sb_secret_abcXYZ123_doNotLog", scrubbed)

    def test_redacts_url_with_embedded_credentials(self):
        scrubbed = bs.scrub_secrets("connect to https://user:hunterpass@example.supabase.co/rest failed")
        self.assertNotIn("hunterpass", scrubbed)

    def test_passes_through_ordinary_text(self):
        self.assertEqual(bs.scrub_secrets("object not found: folder/file.txt"), "object not found: folder/file.txt")

    def test_handles_none(self):
        self.assertEqual(bs.scrub_secrets(None), "")


class ObjectDiscoveryTests(unittest.TestCase):
    def test_empty_bucket_yields_nothing(self):
        bucket = FakeSupabaseBucket(tree={})
        self.assertEqual(list(bs.iter_all_objects(bucket)), [])

    def test_single_file_at_root(self):
        bucket = FakeSupabaseBucket(tree={"a.txt": b"hello"})
        objects = list(bs.iter_all_objects(bucket))
        self.assertEqual(len(objects), 1)
        self.assertEqual(objects[0]["full_path"], "a.txt")

    def test_nested_folders_are_walked_and_never_yielded_as_objects(self):
        tree = {
            "root.txt": b"root-bytes",
            "invoices": {
                "2026": {
                    "jan.pdf": b"jan-bytes",
                    "feb.pdf": b"feb-bytes",
                },
                "readme.txt": b"readme-bytes",
            },
        }
        bucket = FakeSupabaseBucket(tree=tree)
        objects = list(bs.iter_all_objects(bucket))
        paths = sorted(o["full_path"] for o in objects)
        self.assertEqual(paths, ["invoices/2026/feb.pdf", "invoices/2026/jan.pdf", "invoices/readme.txt", "root.txt"])
        # every yielded entry must be a real object, never a folder placeholder
        for entry in objects:
            self.assertFalse(bs.is_folder_entry(entry))

    def test_multiple_files_same_folder_are_all_discovered(self):
        tree = {f"file{i}.bin": f"content-{i}".encode() for i in range(5)}
        bucket = FakeSupabaseBucket(tree=tree)
        objects = list(bs.iter_all_objects(bucket))
        self.assertEqual(len(objects), 5)

    def test_pagination_across_more_than_one_page(self):
        tree = {f"file{i:03d}.bin": b"x" for i in range(bs.LIST_PAGE_SIZE + 5)}
        bucket = FakeSupabaseBucket(tree=tree)
        objects = list(bs.iter_all_objects(bucket))
        self.assertEqual(len(objects), bs.LIST_PAGE_SIZE + 5)
        # confirms more than one page was actually requested
        self.assertGreaterEqual(len(bucket.list_calls), 2)

    def test_list_failure_propagates_to_caller(self):
        bucket = FakeSupabaseBucket(tree={"a.txt": b"x"})
        bucket.list_exception = RuntimeError("Supabase Storage unreachable")
        with self.assertRaises(RuntimeError):
            list(bs.iter_all_objects(bucket))


class R2ObjectKeyTests(unittest.TestCase):
    def test_root_level_file(self):
        self.assertEqual(bs.r2_object_key_for("cauldra-private", "a.txt"), "storage/cauldra-private/a.txt")

    def test_nested_file_preserves_exact_path(self):
        self.assertEqual(
            bs.r2_object_key_for("cauldra-private", "invoices/2026/jan.pdf"),
            "storage/cauldra-private/invoices/2026/jan.pdf",
        )

    def test_never_at_bucket_root(self):
        key = bs.r2_object_key_for("cauldra-private", "a.txt")
        self.assertTrue(key.startswith("storage/cauldra-private/"))


class SupabaseBucketClientTests(EnvIsolatedTestCase):
    def test_uses_supabase_storage_bucket_env_var_when_set(self):
        fake_client = MagicMock()
        fake_settings = MagicMock(secret_key=FAKE_SUPABASE_SECRET)
        os.environ["SUPABASE_STORAGE_BUCKET"] = "some-other-bucket"
        with patch("supabase_client.get_supabase_settings", return_value=fake_settings), \
             patch("supabase_client.get_supabase_client", return_value=fake_client):
            _, bucket_name = bs.get_supabase_bucket_client()
        self.assertEqual(bucket_name, "some-other-bucket")
        os.environ.pop("SUPABASE_STORAGE_BUCKET", None)

    def test_defaults_to_cauldra_private_when_unset(self):
        os.environ.pop("SUPABASE_STORAGE_BUCKET", None)
        fake_client = MagicMock()
        fake_settings = MagicMock(secret_key=FAKE_SUPABASE_SECRET)
        with patch("supabase_client.get_supabase_settings", return_value=fake_settings), \
             patch("supabase_client.get_supabase_client", return_value=fake_client):
            _, bucket_name = bs.get_supabase_bucket_client()
        self.assertEqual(bucket_name, bs.DEFAULT_SUPABASE_BUCKET)

    def test_misconfigured_supabase_raises_backup_error_without_leaking_key(self):
        from supabase_client import SupabaseConfigurationError

        with patch("supabase_client.get_supabase_settings", side_effect=SupabaseConfigurationError("bad key")), \
             patch("supabase_client.get_supabase_client", side_effect=SupabaseConfigurationError("bad key")):
            with self.assertRaises(bs.BackupError):
                bs.get_supabase_bucket_client()

    def test_secret_key_is_registered_for_scrubbing(self):
        fake_client = MagicMock()
        fake_settings = MagicMock(secret_key=FAKE_SUPABASE_SECRET)
        with patch("supabase_client.get_supabase_settings", return_value=fake_settings), \
             patch("supabase_client.get_supabase_client", return_value=fake_client):
            bs.get_supabase_bucket_client()
        self.assertNotIn(FAKE_SUPABASE_SECRET, bs.scrub_secrets(f"failed using {FAKE_SUPABASE_SECRET}"))


class MainOrchestrationTests(EnvIsolatedTestCase):
    def _patch_supabase(self, bucket: FakeSupabaseBucket, bucket_name: str = "cauldra-private"):
        return patch("backup_storage.get_supabase_bucket_client", return_value=(bucket, bucket_name))

    def _patch_r2(self, r2_client: FakeR2Client):
        return patch("backup_storage.build_r2_client", return_value=r2_client)

    def test_missing_r2_configuration_exits_nonzero_and_never_touches_supabase(self):
        with patch("backup_storage.get_supabase_bucket_client") as get_bucket:
            buf = io.StringIO()
            with redirect_stdout(buf):
                exit_code = bs.main()
        self.assertEqual(exit_code, 1)
        get_bucket.assert_not_called()
        self.assertIn("Backup failed", buf.getvalue())

    def test_empty_bucket_reports_no_files_found_and_succeeds(self):
        self.set_full_valid_r2_env()
        bucket = FakeSupabaseBucket(tree={})
        with self._patch_supabase(bucket), self._patch_r2(FakeR2Client()):
            buf = io.StringIO()
            with redirect_stdout(buf):
                exit_code = bs.main()
        self.assertEqual(exit_code, 0)
        output = buf.getvalue()
        self.assertIn("No files found.", output)
        self.assertIn("Backup completed successfully.", output)

    def test_single_file_backed_up_and_verified(self):
        self.set_full_valid_r2_env()
        bucket = FakeSupabaseBucket(tree={"a.txt": b"hello world"})
        r2 = FakeR2Client()
        with self._patch_supabase(bucket), self._patch_r2(r2):
            buf = io.StringIO()
            with redirect_stdout(buf):
                exit_code = bs.main()
        self.assertEqual(exit_code, 0)
        self.assertEqual(r2.objects.get("storage/cauldra-private/a.txt"), b"hello world")
        output = buf.getvalue()
        self.assertIn("files discovered: 1", output)
        self.assertIn("files uploaded:   1", output)
        self.assertIn("files failed:     0", output)
        self.assertIn("total bytes backed up: 11", output)

    def test_nested_paths_preserved_exactly_in_r2(self):
        self.set_full_valid_r2_env()
        bucket = FakeSupabaseBucket(tree={"invoices": {"2026": {"jan.pdf": b"jan-bytes"}}})
        r2 = FakeR2Client()
        with self._patch_supabase(bucket), self._patch_r2(r2):
            buf = io.StringIO()
            with redirect_stdout(buf):
                exit_code = bs.main()
        self.assertEqual(exit_code, 0)
        self.assertIn("storage/cauldra-private/invoices/2026/jan.pdf", r2.objects)

    def test_multiple_files_all_backed_up(self):
        self.set_full_valid_r2_env()
        tree = {"a.txt": b"aaa", "b.txt": b"bb", "folder": {"c.txt": b"c"}}
        bucket = FakeSupabaseBucket(tree=tree)
        r2 = FakeR2Client()
        with self._patch_supabase(bucket), self._patch_r2(r2):
            buf = io.StringIO()
            with redirect_stdout(buf):
                exit_code = bs.main()
        self.assertEqual(exit_code, 0)
        self.assertEqual(len(r2.objects), 3)
        self.assertIn("files discovered: 3", buf.getvalue())
        self.assertIn("total bytes backed up: 6", buf.getvalue())

    def test_supabase_list_failure_exits_nonzero_and_uploads_nothing(self):
        self.set_full_valid_r2_env()
        bucket = FakeSupabaseBucket(tree={"a.txt": b"x"})
        bucket.list_exception = RuntimeError(f"connection failed using {FAKE_SUPABASE_SECRET}")
        r2 = FakeR2Client()
        with self._patch_supabase(bucket), self._patch_r2(r2):
            bs._register_secret(FAKE_SUPABASE_SECRET)
            buf = io.StringIO()
            with redirect_stdout(buf):
                exit_code = bs.main()
        self.assertEqual(exit_code, 1)
        self.assertEqual(len(r2.objects), 0)
        output = buf.getvalue()
        self.assertIn("Backup failed", output)
        self.assertNotIn(FAKE_SUPABASE_SECRET, output)

    def test_supabase_download_failure_marks_one_file_failed_but_continues(self):
        self.set_full_valid_r2_env()
        bucket = FakeSupabaseBucket(tree={"good.txt": b"ok-bytes", "bad.txt": b"unused"})
        bucket.download_exceptions["bad.txt"] = RuntimeError("download failed")
        r2 = FakeR2Client()
        with self._patch_supabase(bucket), self._patch_r2(r2):
            buf = io.StringIO()
            with redirect_stdout(buf):
                exit_code = bs.main()
        self.assertEqual(exit_code, 1)
        self.assertIn("storage/cauldra-private/good.txt", r2.objects)
        self.assertNotIn("storage/cauldra-private/bad.txt", r2.objects)
        output = buf.getvalue()
        self.assertIn("files uploaded:   1", output)
        self.assertIn("files failed:     1", output)
        self.assertIn("Backup completed with failures.", output)

    def test_r2_upload_failure_marks_one_file_failed_but_continues(self):
        self.set_full_valid_r2_env()
        bucket = FakeSupabaseBucket(tree={"good.txt": b"ok", "bad.txt": b"nope"})
        r2 = FakeR2Client()
        r2.put_exceptions["storage/cauldra-private/bad.txt"] = RuntimeError("network unreachable")
        with self._patch_supabase(bucket), self._patch_r2(r2):
            buf = io.StringIO()
            with redirect_stdout(buf):
                exit_code = bs.main()
        self.assertEqual(exit_code, 1)
        self.assertIn("storage/cauldra-private/good.txt", r2.objects)
        self.assertNotIn("storage/cauldra-private/bad.txt", r2.objects)
        self.assertIn("files failed:     1", buf.getvalue())

    def test_r2_verification_size_mismatch_marks_file_failed(self):
        self.set_full_valid_r2_env()
        bucket = FakeSupabaseBucket(tree={"a.txt": b"twelve-bytes"})
        r2 = FakeR2Client()
        r2.head_size_overrides["storage/cauldra-private/a.txt"] = 1  # wrong on purpose
        with self._patch_supabase(bucket), self._patch_r2(r2):
            buf = io.StringIO()
            with redirect_stdout(buf):
                exit_code = bs.main()
        self.assertEqual(exit_code, 1)
        output = buf.getvalue()
        self.assertIn("files failed:     1", output)
        self.assertIn("size mismatch", output)

    def test_non_zero_exit_whenever_any_object_fails(self):
        self.set_full_valid_r2_env()
        bucket = FakeSupabaseBucket(tree={"a.txt": b"ok", "b.txt": b"ok", "c.txt": b"bad"})
        r2 = FakeR2Client()
        r2.put_exceptions["storage/cauldra-private/c.txt"] = RuntimeError("boom")
        with self._patch_supabase(bucket), self._patch_r2(r2):
            with redirect_stdout(io.StringIO()):
                exit_code = bs.main()
        self.assertEqual(exit_code, 1)

    def test_successful_run_never_leaks_r2_or_supabase_secrets_in_output(self):
        self.set_full_valid_r2_env()
        bucket = FakeSupabaseBucket(tree={"a.txt": b"hello", "folder": {"b.txt": b"world"}})
        r2 = FakeR2Client()
        with self._patch_supabase(bucket), self._patch_r2(r2):
            bs._register_secret(FAKE_SUPABASE_SECRET)
            buf = io.StringIO()
            with redirect_stdout(buf):
                exit_code = bs.main()
        self.assertEqual(exit_code, 0)
        output = buf.getvalue()
        self.assertNotIn(FAKE_R2["R2_SECRET_ACCESS_KEY"], output)
        self.assertNotIn(FAKE_R2["R2_ACCESS_KEY_ID"], output)
        self.assertNotIn(FAKE_SUPABASE_SECRET, output)


if __name__ == "__main__":
    unittest.main()
