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
import json
import unittest
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


if __name__ == '__main__':
    unittest.main()
