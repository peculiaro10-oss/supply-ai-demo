"""Fast isolated regression for webhook marker/effect rollback semantics."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SECRET = "test-secret-0123456789abcdef0123456789abcdef0123456789abcdef"


class PaystackWebhookAtomicityTests(unittest.TestCase):
    def test_failure_does_not_poison_retry(self):
        with tempfile.TemporaryDirectory() as temp:
            db_path = Path(temp) / "webhook-atomicity.db"
            env = os.environ | {
                "DATABASE_URL": "",
                "SUPPLY_AI_DATABASE_PATH": str(db_path),
                "SUPPLY_AI_AUTO_CREATE_SCHEMA": "true",
                "SUPPLY_AI_ENV": "development",
                "SUPPLY_AI_SECRET_KEY": SECRET,
                "PAYSTACK_SECRET_KEY": "test-paystack-webhook-secret",
            }
            script = r'''
import hashlib, hmac, json
from datetime import datetime, timedelta
from unittest.mock import patch

import main
from fastapi.testclient import TestClient

reference = "sqlite-webhook-atomicity"
db = main.SessionLocal()
business = main.BusinessProfile(business_code="WH-SQLITE-01", company_name="Webhook SQLite", email="billing@webhook.example.com")
db.add(business); db.flush()
sub = main.BusinessSubscription(
    business_id=business.id, plan="starter", billing_interval="monthly",
    status="pending_payment_method", current_period_start=datetime.utcnow(),
    current_period_end=datetime.utcnow() + timedelta(days=14),
)
db.add(sub); db.flush()
db.add(main.PaymentRecord(
    business_id=business.id, subscription_id=sub.id, plan="starter", billing_interval="monthly",
    amount_kobo=500000, currency="NGN", paystack_reference=reference,
    status="initialized", purpose="subscription",
    transaction_metadata=json.dumps({"business_id": business.id, "plan": "starter", "billing_interval": "monthly",
                                     "purpose": "subscription", "customer_email": "billing@webhook.example.com"}),
))
db.commit(); sub_id = sub.id; business_id = business.id; db.close()

data = {"reference": reference, "status": "success", "amount": 500000, "currency": "NGN", "id": 12345,
        "customer": {"email": "billing@webhook.example.com"},
        "metadata": {"business_id": business_id, "plan": "starter", "billing_interval": "monthly", "purpose": "subscription"}}
raw = json.dumps({"event": "charge.success", "data": data}, separators=(",", ":")).encode()
signature = hmac.new(main.PAYSTACK_SECRET_KEY.encode(), raw, hashlib.sha512).hexdigest()
headers = {"content-type": "application/json", "x-paystack-signature": signature}

with patch.object(main, "paystack_verify_transaction", return_value=data), patch.object(
    main, "add_audit", side_effect=RuntimeError("injected post-effect failure")
):
    failed = TestClient(main.app, raise_server_exceptions=False).post("/webhooks/paystack", content=raw, headers=headers)
assert failed.status_code == 500, failed.text

db = main.SessionLocal()
assert db.query(main.PaystackWebhookEvent).count() == 0
assert db.query(main.PaymentRecord).filter_by(paystack_reference=reference).one().status == "initialized"
assert db.query(main.BusinessSubscription).filter_by(id=sub_id).one().status == "pending_payment_method"
db.close()

with patch.object(main, "paystack_verify_transaction", return_value=data):
    received = TestClient(main.app).post("/webhooks/paystack", content=raw, headers=headers)
    duplicate = TestClient(main.app).post("/webhooks/paystack", content=raw, headers=headers)
assert received.status_code == 200 and received.json()["status"] == "received", received.text
assert duplicate.status_code == 200 and duplicate.json()["status"] == "already_processed", duplicate.text

db = main.SessionLocal()
assert db.query(main.PaystackWebhookEvent).count() == 1
assert db.query(main.PaymentRecord).filter_by(paystack_reference=reference).one().status == "success"
assert db.query(main.BusinessSubscription).filter_by(id=sub_id).one().status == "active"
assert db.query(main.AuditLog).filter_by(business_id=business_id, action="SUBSCRIPTION_ACTIVATED").count() == 1
db.close()
'''
            result = subprocess.run(
                [sys.executable, "-c", script], cwd=ROOT, env=env,
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
