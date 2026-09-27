"""Local end-to-end tests only (tests/run_offline_external_e2e.js): a simulated
email provider. Loaded only when this directory is put on PYTHONPATH AND
CAULDRA_TEST_FAKE_EMAIL_PROVIDER=1; it is never deployed.

Every request to Resend's API is answered locally and never leaves the
machine. Each call is appended to CAULDRA_TEST_FAKE_EMAIL_LOG (headers other
than Idempotency-Key are dropped, so no key is ever written). The HTTP status
to answer with is read from CAULDRA_TEST_FAKE_EMAIL_STATUS_FILE (default 200),
so a test can simulate an outage and then a recovery. Like Resend, a repeated
Idempotency-Key is answered without a second delivery.
"""
import json
import os

if os.getenv("CAULDRA_TEST_FAKE_EMAIL_PROVIDER") == "1":
    import requests

    _real_post = requests.post
    _delivered_keys = set()

    class _Response:
        def __init__(self, status, body):
            self.status_code = status
            self.ok = 200 <= status < 300
            self._body = body
            self.headers = {}

        def json(self):
            return self._body

    def _status():
        try:
            with open(os.environ["CAULDRA_TEST_FAKE_EMAIL_STATUS_FILE"]) as handle:
                return int(handle.read().strip() or 200)
        except (KeyError, OSError, ValueError):
            return 200

    def _fake_post(url, *args, **kwargs):
        if not str(url).startswith("https://api.resend.com/"):
            return _real_post(url, *args, **kwargs)
        status = _status()
        key = (kwargs.get("headers") or {}).get("Idempotency-Key")
        duplicate = bool(key) and key in _delivered_keys and status < 300
        if status < 300 and key:
            _delivered_keys.add(key)
        record = {"to": (kwargs.get("json") or {}).get("to"), "subject": (kwargs.get("json") or {}).get("subject"),
                  "idempotency_key": key, "status": status, "delivered": status < 300 and not duplicate}
        with open(os.environ.get("CAULDRA_TEST_FAKE_EMAIL_LOG", os.devnull), "a") as handle:
            handle.write(json.dumps(record) + "\n")
        if status < 300:
            return _Response(status, {"id": "simulated"})
        return _Response(status, {"name": "application_error", "message": "simulated outage"})

    requests.post = _fake_post
