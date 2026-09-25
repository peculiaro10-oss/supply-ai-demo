"""QA-STORAGE-001 — customer upload bytes live in durable private storage.

Commit 509698d disconnected backend/storage.py, so every upload went to the
container's own disk and each redeploy deleted it while the metadata row
survived. These tests pin the reconnection:

  * main.py stores, reads, replaces and deletes invoice, price-list and
    profile-photo bytes ONLY through the configured provider;
  * a failed transaction removes the object it stored, and a replaced or
    removed object goes only once the database change has committed;
  * a deployed environment (anything but development/test) refuses to start
    on container-local storage, and requires the Supabase bucket be private;
  * Batch B's tenant, role and guest rules are unchanged on the Supabase
    provider (the X3 visibility suite is re-run against it).

The Supabase client is an in-memory fake with the storage3 call shapes; real
Supabase behaviour is proven on QA across a redeploy.
"""
import os
import tempfile
os.environ['PYTHON_DOTENV_DISABLED'] = '1'
os.environ['SUPPLY_AI_SKIP_DB_STARTUP_CHECK'] = 'true'
os.environ['DATABASE_URL'] = 'postgresql+psycopg://test:test@127.0.0.1:65432/cauldra_test'
os.environ['SUPPLY_AI_SECRET_KEY'] = 'isolated-test-secret-012345678901234567890123456789'
os.environ.setdefault('SUPPLY_AI_UPLOAD_DIR', tempfile.mkdtemp(prefix='cauldra-storage-'))
import base64
import hashlib
import re
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

import main
import storage
from tests import test_batch_b_security as bb

ROOT = Path(__file__).resolve().parents[1]
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC')


# --------------------------------------------------------------- fake Supabase
class FakeStorageApiError(Exception):
    """Same attributes as storage3.exceptions.StorageApiError."""
    def __init__(self, message, code, status):
        super().__init__(message)
        self.message, self.code, self.status = message, code, status


class FakeBucketApi:
    def __init__(self, backend):
        self.backend = backend

    def upload(self, path, file, file_options=None):
        if self.backend.fail_uploads:
            raise FakeStorageApiError('upstream unavailable', 'InternalError', 500)
        if path in self.backend.objects:
            raise FakeStorageApiError('The resource already exists', 'Duplicate', '409')
        self.backend.objects[path] = (bytes(file), (file_options or {}).get('content-type'))
        self.backend.calls.append(('upload', path))

    def download(self, path):
        self.backend.calls.append(('download', path))
        if self.backend.fail_downloads:
            raise FakeStorageApiError('upstream unavailable', 'InternalError', 500)
        if path not in self.backend.objects:
            raise FakeStorageApiError('Object not found', 'not_found', '404')
        return self.backend.objects[path][0]

    def remove(self, paths):
        self.backend.calls.append(('remove', tuple(paths)))
        for p in paths:
            self.backend.objects.pop(p, None)
        return []


class FakeStorageNamespace:
    def __init__(self, backend):
        self.backend = backend

    def from_(self, bucket):
        assert bucket == self.backend.bucket, bucket
        return FakeBucketApi(self.backend)

    def get_bucket(self, name):
        if name != self.backend.bucket:
            raise FakeStorageApiError('Bucket not found', 'not_found', '404')
        return {'id': name, 'name': name, 'public': self.backend.public}


class FakeSupabase:
    def __init__(self, bucket='cauldra-private', public=False):
        self.bucket, self.public = bucket, public
        self.objects, self.calls = {}, []
        self.fail_uploads = self.fail_downloads = False
        self.storage = FakeStorageNamespace(self)


def supabase_provider(fake):
    return storage.SupabaseStorage(fake.bucket, client_factory=lambda: fake)


class SupabaseBacked(bb.BatchBBase):
    """Batch B fixtures with main.UPLOAD_STORAGE swapped for a Supabase provider."""
    def setUp(self):
        self.fake = FakeSupabase()
        p = patch.object(main, 'UPLOAD_STORAGE', supabase_provider(self.fake))
        p.start(); self.addCleanup(p.stop)
        super().setUp()

    def data_url(self, raw, mime):
        return f'data:{mime};base64,' + base64.b64encode(raw).decode()

    def row(self, upload_id):
        self.db.expire_all()
        return self.db.query(main.StoredUpload).filter_by(id=upload_id).one_or_none()

    def local_files(self):
        return [p for p in main.UPLOAD_STORAGE_DIR.rglob('*') if p.is_file()]


# ============================================== 1-9: all three types, durable
class DurableUploadTypesTests(SupabaseBacked):
    def upload_avatar(self, user, raw=PNG, client=None):
        return (client or self.client).post('/users/me/avatar', headers=self.auth(user),
                                            json={'file_name': 'me.png', 'file_data': self.data_url(raw, 'image/png')})

    def avatar_id(self, user):
        self.db.expire_all()
        return self.db.get(main.User, user.id).avatar_upload_id

    def test_profile_photo_is_stored_in_the_bucket_and_read_back_identically(self):
        before = set(self.local_files())
        r = self.upload_avatar(self.staff)
        self.assertEqual(r.status_code, 200, r.text)
        row = self.row(self.avatar_id(self.staff))
        self.assertRegex(row.storage_key, rf'^{self.biz_a.id}/{row.id}-[A-Za-z0-9_-]+\.png$')
        self.assertEqual(self.fake.objects[row.storage_key], (PNG, 'image/png'))
        self.assertEqual(set(self.local_files()), before, 'nothing may be written to container disk')
        got = self.client.get('/users/me/avatar', headers=self.auth(self.staff))
        self.assertEqual(got.status_code, 200)
        self.assertEqual(hashlib.sha256(got.content).hexdigest(), hashlib.sha256(PNG).hexdigest())
        self.assertEqual(got.headers['content-type'], 'image/png')
        self.assertEqual(got.headers['content-disposition'], 'inline')
        self.assertEqual(row.content_hash, hashlib.sha256(PNG).hexdigest())

    def test_invoice_scan_upload_is_stored_in_the_bucket(self):
        raw = PNG + b'invoice'
        with patch.object(main, 'run_billable_ai', return_value=({'items': []}, 0)):
            r = self.client.post('/ai/scan-invoice', headers=self.auth(self.admin),
                                 json={'file_name': 'inv.png', 'image_data': self.data_url(raw, 'image/png')})
        self.assertEqual(r.status_code, 200, r.text)
        row = self.row(r.json()['upload_id'])
        self.assertEqual(row.kind, 'invoice')
        self.assertEqual(self.fake.objects[row.storage_key][0], raw)
        d = self.client.get(f'/uploads/{row.id}/download', headers=self.auth(self.admin))
        self.assertEqual(d.status_code, 200)
        self.assertEqual(d.content, raw)
        self.assertEqual(d.headers['content-disposition'], 'attachment; filename="inv.png"')

    def test_price_list_upload_is_stored_in_the_bucket(self):
        supplier = main.Supplier(name='Kano Traders', phone='+2348031111111', business_id=self.biz_a.id)
        product = main.Product(name='Rice', sku='RICE-1', category='Grains', business_id=self.biz_a.id,
                               cost_price=10.0, retail_price=12.0, quantity=1, min_stock_level=1)
        self.db.add_all([supplier, product]); self.db.commit()
        body = b'sku,price\nRICE-1,11\n'
        r = self.client.post('/price-monitor/upload-price-list', headers=self.auth(self.admin),
                             json={'supplier_id': supplier.id, 'product_id': None, 'file_name': 'prices.csv',
                                   'file_data': self.data_url(body, 'text/csv')})
        self.assertEqual(r.status_code, 200, r.text)
        row = self.row(r.json()['upload_id'])
        self.assertEqual(row.kind, 'price_list')
        self.assertEqual(self.fake.objects[row.storage_key][0], body)
        d = self.client.get(f'/uploads/{row.id}/download', headers=self.auth(self.admin))
        self.assertEqual((d.status_code, d.content), (200, body))

    def test_replacing_a_profile_photo_removes_the_old_object_after_commit(self):
        self.upload_avatar(self.staff)
        old = self.row(self.avatar_id(self.staff))
        old_id, old_key = old.id, old.storage_key
        new_raw = PNG + b'second'
        self.assertEqual(self.upload_avatar(self.staff, new_raw).status_code, 200)
        new = self.row(self.avatar_id(self.staff))
        self.assertNotEqual(new.id, old_id)
        self.assertIsNone(self.row(old_id))
        self.assertNotIn(old_key, self.fake.objects)
        self.assertEqual(self.fake.objects[new.storage_key][0], new_raw)
        self.assertEqual(self.client.get('/users/me/avatar', headers=self.auth(self.staff)).content, new_raw)

    def test_a_failed_replacement_keeps_the_old_photo_and_drops_the_new_object(self):
        self.upload_avatar(self.staff)
        old = self.row(self.avatar_id(self.staff))
        client = TestClient(main.app, raise_server_exceptions=False)
        with patch.object(main, 'add_audit', side_effect=RuntimeError('db write failed')):
            self.assertEqual(self.upload_avatar(self.staff, PNG + b'second', client=client).status_code, 500)
        client.close()
        self.assertEqual(self.avatar_id(self.staff), old.id)
        self.assertEqual(list(self.fake.objects), [old.storage_key], 'old object kept, new object cleaned up')
        self.assertEqual(self.client.get('/users/me/avatar', headers=self.auth(self.staff)).content, PNG)

    def test_removing_a_profile_photo_deletes_row_and_object(self):
        self.upload_avatar(self.staff)
        row = self.row(self.avatar_id(self.staff))
        r = self.client.delete('/users/me/avatar', headers=self.auth(self.staff))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIsNone(self.row(row.id))
        self.assertEqual(self.fake.objects, {})
        self.assertEqual(self.client.get('/users/me/avatar', headers=self.auth(self.staff)).status_code, 404)

    def test_a_rolled_back_upload_leaves_no_object_behind(self):
        with self.Session() as s:
            user = s.get(main.User, self.admin.id)
            row = main.persist_upload(s, user, 'invoice', 'x.png', 'image/png', PNG)
            key = row.storage_key
            self.assertIn(key, self.fake.objects)
            s.rollback()
        self.assertNotIn(key, self.fake.objects)

    def test_an_abandoned_session_leaves_no_object_behind(self):
        s = self.Session()
        row = main.persist_upload(s, s.get(main.User, self.admin.id), 'invoice', 'x.png', 'image/png', PNG)
        key = row.storage_key
        s.close()          # request ended with an exception before commit
        self.assertNotIn(key, self.fake.objects)

    def test_a_failed_price_list_after_storing_cleans_up(self):
        supplier = main.Supplier(name='Kano Traders', phone='+2348031111111', business_id=self.biz_a.id)
        product = main.Product(name='Rice', sku='RICE-1', category='Grains', business_id=self.biz_a.id,
                               cost_price=10.0, retail_price=12.0, quantity=1, min_stock_level=1)
        self.db.add_all([supplier, product]); self.db.commit()
        client = TestClient(main.app, raise_server_exceptions=False)
        with patch.object(main, 'add_audit', side_effect=RuntimeError('db write failed')):
            r = client.post('/price-monitor/upload-price-list', headers=self.auth(self.admin),
                            json={'supplier_id': supplier.id, 'product_id': None, 'file_name': 'p.csv',
                                  'file_data': self.data_url(b'sku,price\nRICE-1,11\n', 'text/csv')})
        client.close()
        self.assertEqual(r.status_code, 500)
        self.assertEqual(self.fake.objects, {})
        self.assertEqual(self.db.query(main.StoredUpload).count(), 0)

    def test_a_committed_upload_is_not_removed_by_later_session_end(self):
        with self.Session() as s:
            row = main.persist_upload(s, s.get(main.User, self.admin.id), 'invoice', 'x.png', 'image/png', PNG)
            key = row.storage_key
            s.commit()
        self.assertIn(key, self.fake.objects)

    def test_storage_outage_on_upload_is_a_503_with_no_row(self):
        self.fake.fail_uploads = True
        r = self.upload_avatar(self.staff)
        self.assertEqual(r.status_code, 503)
        self.assertEqual(self.db.query(main.StoredUpload).count(), 0)

    def test_missing_object_is_a_controlled_404_and_outage_a_503(self):
        self.upload_avatar(self.staff)
        row = self.row(self.avatar_id(self.staff))
        self.fake.fail_downloads = True
        self.assertEqual(self.client.get('/users/me/avatar', headers=self.auth(self.staff)).status_code, 503)
        self.fake.fail_downloads = False
        self.fake.objects.clear()     # the QA-STORAGE-001 state: row survives, bytes gone
        r = self.client.get('/users/me/avatar', headers=self.auth(self.staff))
        self.assertEqual((r.status_code, r.json()['detail']), (404, 'No profile photo set.'))
        self.assertIsNotNone(self.row(row.id), 'a missing object never deletes or rewrites its row')

    def test_business_deletion_removes_its_objects_only(self):
        self.upload_avatar(self.staff)
        self.upload_avatar(self.admin_b)
        b_key = self.row(self.avatar_id(self.admin_b)).storage_key
        r = self.client.delete('/business-profile/', headers=self.auth(self.admin))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(list(self.fake.objects), [b_key])

    def test_unsafe_file_extensions_never_reach_the_object_key(self):
        with self.Session() as s:
            row = main.persist_upload(s, s.get(main.User, self.admin.id), 'invoice', 'a.p?n g', 'image/png', PNG)
            self.assertRegex(row.storage_key, r'^\d+/\d+-[A-Za-z0-9_-]+$')
            s.rollback()


# ========================================== 12-14: Batch B X3 on the provider
class SupabaseUploadVisibilityTests(bb.UploadVisibilityTests):
    """The Batch B role/tenant/guest download matrix, unchanged, with the
    bytes served from the Supabase provider instead of local disk."""
    def setUp(self):
        super().setUp()
        self.fake = FakeSupabase()
        for row in self.db.query(main.StoredUpload).all():
            self.fake.objects[row.storage_key] = ((main.UPLOAD_STORAGE_DIR / row.storage_key).read_bytes(), 'image/png')
            (main.UPLOAD_STORAGE_DIR / row.storage_key).unlink()   # prove the disk is not the source
        p = patch.object(main, 'UPLOAD_STORAGE', supabase_provider(self.fake))
        p.start(); self.addCleanup(p.stop)

    def test_download_bytes_come_from_the_bucket(self):
        r = self.client.get(f"/uploads/{self.files['staff_invoice']}/download", headers=self.auth(self.staff))
        self.assertEqual((r.status_code, r.content), (200, b'content of staff_invoice'))
        self.assertTrue(any(c[0] == 'download' for c in self.fake.calls))

    def test_a_denied_request_never_reaches_the_bucket(self):
        self.fake.calls.clear()
        self.assertEqual(self.download(self.admin_b, 'admin_invoice'), 404)
        self.assertEqual(self.client.get(f"/uploads/{self.files['admin_invoice']}/download").status_code, 401)
        self.assertEqual(self.download(self.staff, 'admin_invoice'), 404)
        self.assertEqual(self.fake.calls, [])


# ======================================================= provider unit tests
class ProviderTests(unittest.TestCase):
    def test_supabase_provider_round_trip_delete_and_missing(self):
        fake = FakeSupabase()
        p = supabase_provider(fake)
        self.assertTrue(p.durable)
        p.put_bytes('7/1-abc.png', PNG, 'image/png')
        self.assertEqual(p.read_bytes('7/1-abc.png'), PNG)
        p.delete('7/1-abc.png')
        with self.assertRaises(storage.StorageObjectNotFound):
            p.read_bytes('7/1-abc.png')

    def test_supabase_provider_rejects_traversal_keys(self):
        p = supabase_provider(FakeSupabase())
        for key in ('../x', '/abs', '7/../../x', 'c:/x', '7//x', ''):
            with self.subTest(key=key), self.assertRaises(ValueError):
                p.put_bytes(key, b'x', 'text/plain')

    def test_verify_requires_an_existing_private_bucket(self):
        supabase_provider(FakeSupabase(public=False)).verify()
        with self.assertRaises(RuntimeError):
            supabase_provider(FakeSupabase(public=True)).verify()
        missing = FakeSupabase()
        with self.assertRaises(Exception):
            storage.SupabaseStorage('not-there', client_factory=lambda: missing).verify()

    def test_local_provider_is_not_durable_and_reports_missing(self):
        with tempfile.TemporaryDirectory() as temp:
            p = storage.LocalStorage(Path(temp))
            self.assertFalse(p.durable)
            with self.assertRaises(storage.StorageObjectNotFound):
                p.read_bytes('1/none.bin')

    def test_the_guard_allows_local_only_in_development_and_test(self):
        with tempfile.TemporaryDirectory() as temp:
            local = storage.LocalStorage(Path(temp))
            for env in ('development', 'dev', 'local', 'test', 'testing', 'Development'):
                storage.enforce_durable_upload_storage(local, env)
            for env in ('staging', 'production', 'qa', '', 'prod'):
                with self.subTest(env=env), self.assertRaises(RuntimeError):
                    storage.enforce_durable_upload_storage(local, env)
        for env in ('staging', 'production'):
            storage.enforce_durable_upload_storage(supabase_provider(FakeSupabase()), env)

    def test_main_never_touches_upload_bytes_on_disk_directly(self):
        source = (ROOT / 'backend' / 'main.py').read_text(encoding='utf-8')
        self.assertNotRegex(source, r'UPLOAD_STORAGE_DIR\s*/')
        self.assertIn('UPLOAD_STORAGE = build_storage_provider(UPLOAD_STORAGE_DIR)', source)
        for call in ('UPLOAD_STORAGE.put_bytes', 'UPLOAD_STORAGE.read_bytes', 'UPLOAD_STORAGE.delete'):
            self.assertIn(call, source)


# ============================================ 10-11: real startup, subprocess
def start(**overrides):
    env = {k: v for k, v in os.environ.items() if not k.startswith(('SUPABASE_', 'SUPPLY_AI_STORAGE'))}
    env.update({
        'PYTHON_DOTENV_DISABLED': '1',
        'DATABASE_URL': 'postgresql+psycopg://test:test@127.0.0.1:65432/cauldra_test',
        'SUPPLY_AI_SECRET_KEY': 'isolated-test-secret-012345678901234567890123456789',
        'SUPPLY_AI_SKIP_DB_STARTUP_CHECK': 'true',
        'SUPPLY_AI_UPLOAD_DIR': tempfile.mkdtemp(prefix='cauldra-start-'),
        'SUPPLY_AI_TRUSTED_HOSTS': 'app.example.test',
        'SUPPLY_AI_REFRESH_COOKIE_SECURE': 'true',
        'SUPPLY_AI_CORS_ORIGINS': 'https://app.example.test',
        'SUPPLY_AI_FRONTEND_URL': 'https://app.example.test',
        'ONBOARDING_EMAIL_CALLBACK_BASE_URL': 'https://api.example.test',
        'RESEND_FROM': 'Cauldra <no-reply@notify.example.test>',
        'PYTHONPATH': os.pathsep.join([str(ROOT / 'backend'), str(ROOT)]),
    })
    env.pop('SUPPLY_AI_ENV', None)
    env.update(overrides)
    return subprocess.run([sys.executable, '-c', 'import main; print("PROVIDER", main.UPLOAD_STORAGE.name)'],
                          cwd=ROOT, env=env, text=True, capture_output=True, timeout=240)


SUPABASE_ENV = {
    'SUPPLY_AI_STORAGE_BACKEND': 'supabase',
    'SUPABASE_URL': 'https://unit-test-project.supabase.co',
    'SUPABASE_SECRET_KEY': 'sb_secret_UNITTESTONLY0123456789abcdefghijklmnop',
    'SUPABASE_STORAGE_BUCKET': 'cauldra-private',
}


class StartupGuardTests(unittest.TestCase):
    def test_deployed_environments_refuse_container_local_storage(self):
        for env in ('staging', 'production'):
            for backend in (None, 'local'):
                with self.subTest(env=env, backend=backend):
                    extra = {'SUPPLY_AI_ENV': env}
                    if backend:
                        extra['SUPPLY_AI_STORAGE_BACKEND'] = backend
                    r = start(**extra)
                    self.assertNotEqual(r.returncode, 0, r.stdout)
                    self.assertIn('requires durable upload storage', r.stderr)

    def test_supabase_without_a_bucket_refuses_to_start(self):
        env = dict(SUPABASE_ENV, SUPPLY_AI_ENV='staging')
        env.pop('SUPABASE_STORAGE_BUCKET')
        r = start(**env)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('SUPABASE_STORAGE_BUCKET is required', r.stderr)

    def test_deployed_environment_with_supabase_starts_on_supabase(self):
        for env in ('staging', 'production'):
            with self.subTest(env=env):
                r = start(**dict(SUPABASE_ENV, SUPPLY_AI_ENV=env, SUPPLY_AI_STORAGE_BACKEND='Supabase'))
                self.assertEqual(r.returncode, 0, r.stderr[-800:])
                self.assertIn('PROVIDER supabase', r.stdout)
                self.assertNotIn('sb_secret_', r.stdout + r.stderr)

    def test_development_and_tests_still_use_local_storage(self):
        for extra in ({}, {'SUPPLY_AI_ENV': 'development'}, {'SUPPLY_AI_ENV': 'test'}):
            with self.subTest(**extra):
                r = start(**extra)
                self.assertEqual(r.returncode, 0, r.stderr[-800:])
                self.assertIn('PROVIDER local', r.stdout)


if __name__ == '__main__':
    unittest.main()
