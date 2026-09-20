"""OCR-001 — a failure at the document-reading provider must be a readable
error, never an unhandled 500.

An unhandled exception is emitted above CORSMiddleware, so the browser and the
Android client never see a status code at all: they report a connectivity
problem for a server-side fault. No network: the provider client is stubbed.
"""
import os
os.environ['PYTHON_DOTENV_DISABLED'] = '1'
os.environ['SUPPLY_AI_SKIP_DB_STARTUP_CHECK'] = 'true'
os.environ['DATABASE_URL'] = 'postgresql+psycopg://test:test@127.0.0.1:65432/cauldra_test'
os.environ['SUPPLY_AI_SECRET_KEY'] = 'isolated-test-secret-012345678901234567890123456789'
import tempfile as _tempfile
os.environ.setdefault('SUPPLY_AI_UPLOAD_DIR', _tempfile.mkdtemp(prefix='cauldra-invoice-'))
import contextlib
import io as _io
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient
from fastapi import HTTPException
import main


class _Responses:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class _Client:
    def __init__(self, outcome):
        self.responses = _Responses(outcome)


class _Resp:
    def __init__(self, text, usage=None):
        self.output_text = text
        self.usage = usage


class _Usage:
    input_tokens = 812
    output_tokens = 96


class ProviderFailureTests(unittest.TestCase):
    def setUp(self):
        self.original = main.openai_client
        self.addCleanup(setattr, main, 'openai_client', self.original)

    def _scan(self, outcome):
        main.openai_client = _Client(outcome)
        return main.openai_json_response('extract this', image_data='data:image/png;base64,AAAA')

    def test_an_exhausted_provider_quota_is_a_502_not_a_crash(self):
        """The exact failure QA reproduces: 429 insufficient_quota."""
        class RateLimitError(Exception):
            pass
        with self.assertRaises(HTTPException) as caught:
            self._scan(RateLimitError('Error code: 429 - You have no credits remaining.'))
        self.assertEqual(caught.exception.status_code, 502)
        self.assertIn('could not complete that scan', caught.exception.detail)

    def test_the_provider_message_is_never_forwarded_to_the_customer(self):
        class RateLimitError(Exception):
            pass
        with self.assertRaises(HTTPException) as caught:
            self._scan(RateLimitError('no credits remaining. Add credits at https://platform.openai.com/settings/billing'))
        self.assertNotIn('credits remaining', caught.exception.detail)
        self.assertNotIn('http', caught.exception.detail)

    def test_a_transport_failure_is_also_a_502(self):
        with self.assertRaises(HTTPException) as caught:
            self._scan(ConnectionError('connection reset'))
        self.assertEqual(caught.exception.status_code, 502)

    def test_an_unconfigured_provider_is_still_503(self):
        main.openai_client = None
        with self.assertRaises(HTTPException) as caught:
            main.openai_json_response('extract this')
        self.assertEqual(caught.exception.status_code, 503)

    def test_an_uninterpretable_reply_is_still_502(self):
        with self.assertRaises(HTTPException) as caught:
            self._scan(_Resp('not json at all'))
        self.assertEqual(caught.exception.status_code, 502)
        self.assertIn('review it manually', caught.exception.detail)

    def test_an_http_exception_from_inside_is_not_reclassified(self):
        with self.assertRaises(HTTPException) as caught:
            self._scan(HTTPException(status_code=413, detail='Too large.'))
        self.assertEqual(caught.exception.status_code, 413)

    def test_a_successful_scan_is_unchanged(self):
        payload = {'supplier_name': 'Kano Traders', 'invoice_number': 'INV-9', 'invoice_date': '2026-09-20',
                   'items': [{'description': 'Rice 50kg', 'quantity': 2, 'unit_price': 38000}],
                   'subtotal': 76000, 'total': 76000}
        main.openai_client = _Client(_Resp(json.dumps(payload), usage=_Usage()))
        usage = {}
        data = main.openai_json_response('extract this', image_data='data:image/png;base64,AAAA', usage_out=usage)
        self.assertEqual(data['supplier_name'], 'Kano Traders')
        self.assertEqual(len(data['items']), 1)
        self.assertEqual(usage, {'input_tokens': 812, 'output_tokens': 96})

    def test_the_image_still_reaches_the_provider(self):
        main.openai_client = _Client(_Resp('{}'))
        main.openai_json_response('extract this', image_data='data:image/png;base64,AAAA')
        sent = main.openai_client.responses.create.__self__.calls[0]
        content = sent['input'][0]['content']
        self.assertEqual(content[0]['type'], 'input_text')
        self.assertEqual(content[1]['type'], 'input_image')
        self.assertEqual(content[1]['image_url'], 'data:image/png;base64,AAAA')


class OperatorDiagnosticsTests(unittest.TestCase):
    """The customer sees a generic message; the operator must still be able to
    tell an exhausted quota from a rate limit, from the service log alone."""

    def setUp(self):
        self.original = main.openai_client
        self.addCleanup(setattr, main, 'openai_client', self.original)

    def _log_of(self, outcome, image='data:image/png;base64,SECRETIMAGEBYTES'):
        main.openai_client = _Client(outcome)
        buffer = _io.StringIO()
        with contextlib.redirect_stdout(buffer):
            with self.assertRaises(HTTPException):
                main.openai_json_response('extract this invoice', image_data=image)
        return buffer.getvalue()

    def test_the_provider_reason_is_logged_for_the_operator(self):
        class RateLimitError(Exception):
            pass
        logged = self._log_of(RateLimitError('Error code: 429 - insufficient_quota: no credits remaining'))
        self.assertIn('[ai-provider] openai invoice-scan failed', logged)
        self.assertIn('RateLimitError', logged)
        self.assertIn('insufficient_quota', logged)

    def test_the_log_never_carries_the_prompt_or_the_image(self):
        logged = self._log_of(ConnectionError('connection reset'))
        self.assertNotIn('SECRETIMAGEBYTES', logged)
        self.assertNotIn('extract this invoice', logged)

    def test_a_long_provider_message_is_truncated(self):
        logged = self._log_of(ValueError('x' * 5000))
        self.assertLess(len(logged), 500)

    def test_an_uninterpretable_reply_is_logged_distinctly(self):
        logged = self._log_of(_Resp('not json at all'))
        self.assertIn('invoice-parse failed', logged)

    def test_the_gemini_helper_logs_the_same_way(self):
        original = main.gemini_client
        self.addCleanup(setattr, main, 'gemini_client', original)

        class _Models:
            def generate_content(self, **kwargs):
                raise RuntimeError('429 RESOURCE_EXHAUSTED: quota exceeded')

        class _Gemini:
            models = _Models()

        main.gemini_client = _Gemini()
        buffer = _io.StringIO()
        with contextlib.redirect_stdout(buffer):
            with self.assertRaises(HTTPException) as caught:
                main.gemini_text_response('system', 'a prompt the log must not keep')
        self.assertEqual(caught.exception.status_code, 502)
        logged = buffer.getvalue()
        self.assertIn('[ai-provider] gemini text failed', logged)
        self.assertIn('RESOURCE_EXHAUSTED', logged)
        self.assertNotIn('a prompt the log must not keep', logged)


class BilledUsageTests(unittest.TestCase):
    """A failed provider call must not be billed as successful work."""

    def test_run_billable_ai_records_failure_and_re_raises(self):
        recorded = []

        class _DB:
            def commit(self):
                recorded.append('commit')

        original = main.record_ai_usage
        main.record_ai_usage = lambda db, user, op, ok, provider, model, **kw: recorded.append(ok)
        self.addCleanup(setattr, main, 'record_ai_usage', original)

        def boom(usage):
            raise HTTPException(status_code=502, detail='provider down')

        with self.assertRaises(HTTPException) as caught:
            main.run_billable_ai(_DB(), None, 'invoice_ocr', 'openai', 'model-x', boom)
        self.assertEqual(caught.exception.status_code, 502)
        self.assertIn(False, recorded)
        self.assertNotIn(True, recorded)


class ActivityTrailTests(unittest.TestCase):
    """The audit filed this inside OCR-001: five failed scans appeared in
    Activity History as five successful uploads, with no sign of failure."""

    def setUp(self):
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        main.Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

        def db_dep():
            with self.Session() as session:
                yield session
        main.app.dependency_overrides[main.get_db] = db_dep
        self.db = self.Session()
        self.business = main.BusinessProfile(business_code='OC-1', company_name='Scanner Ltd', currency='NGN (₦)',
                                             subscription_plan='business')
        self.db.add(self.business); self.db.commit()
        now = datetime.utcnow()
        self.db.add(main.BusinessSubscription(business_id=self.business.id, plan='business', billing_interval='monthly',
                                              status='active', current_period_start=now,
                                              current_period_end=now + timedelta(days=30), card_verified=True))
        self.db.commit()
        self.admin = main.User(username='owner', email='owner@example.com', password='x', phone='+2348030000001',
                               role='admin', business_id=self.business.id)
        self.db.add(self.admin); self.db.commit(); self.db.refresh(self.admin)
        self.client = TestClient(main.app)
        self.original = main.openai_client
        self.addCleanup(setattr, main, 'openai_client', self.original)

    def tearDown(self):
        self.client.close(); main.app.dependency_overrides.clear()
        self.db.close(); self.engine.dispose()

    PNG = ('data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8'
           'z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==')

    def scan(self):
        return self.client.post('/ai/scan-invoice', json={'image_data': self.PNG, 'file_name': 'invoice.png'},
                                headers={'Authorization': f'Bearer {main.issue_token(self.admin, self.db)}'})

    def actions(self):
        return [a.action for a in self.db.query(main.AuditLog).order_by(main.AuditLog.id).all()]

    def test_a_failed_scan_is_not_recorded_as_a_plain_upload(self):
        class RateLimitError(Exception):
            pass
        main.openai_client = _Client(RateLimitError('429 insufficient_quota'))
        with contextlib.redirect_stdout(_io.StringIO()):
            r = self.scan()
        self.assertEqual(r.status_code, 502, r.text)
        self.assertIn('INVOICE_UPLOADED', self.actions())
        self.assertIn('INVOICE_SCAN_FAILED', self.actions(),
                      'Activity History must show that the scan failed, not just that a file arrived')

    def test_a_successful_scan_records_no_failure(self):
        payload = {'supplier_name': 'Kano Traders', 'invoice_number': 'INV-9', 'invoice_date': '2026-09-20',
                   'items': [], 'subtotal': 0, 'total': 0}
        main.openai_client = _Client(_Resp(json.dumps(payload)))
        r = self.scan()
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn('INVOICE_UPLOADED', self.actions())
        self.assertNotIn('INVOICE_SCAN_FAILED', self.actions())

    def test_the_failure_entry_names_the_file_and_no_provider_detail(self):
        main.openai_client = _Client(ConnectionError('openai host unreachable: sk-should-never-appear'))
        with contextlib.redirect_stdout(_io.StringIO()):
            self.scan()
        entry = self.db.query(main.AuditLog).filter_by(action='INVOICE_SCAN_FAILED').one()
        self.assertIn('invoice.png', entry.description)
        self.assertNotIn('sk-should-never-appear', entry.description)


if __name__ == '__main__':
    unittest.main()
