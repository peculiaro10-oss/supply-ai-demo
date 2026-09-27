"""OFFLINE-EXTERNAL-001: a purchase-order email saved offline, replayed through
the real /offline/replay endpoint against a SIMULATED email provider (no real
Resend call is ever made: requests.post is replaced for every test).

The queued email is a request, not a result: the purchase order becomes SENT
only after the provider accepted it, exactly once, with a provider idempotency
key tied to the saved change so a retry is never a second email. It is refused
(nothing sent) when it is stale, when the draft or supplier changed, when the
order was already sent or deleted, when the user lost po.send, and when the
provider is unconfigured or refuses it; a provider outage is retried.

Set TEST_POSTGRES_ADMIN_URL (see tests/postgres_test_support.py).
"""
from __future__ import annotations

import hashlib
import json
import os
import unittest
import uuid
from datetime import timedelta
from unittest import mock

from tests.postgres_test_support import ADMIN_URL, create_postgres_test_schema, drop_postgres_test_schema
# Helpers are reached through the module (no TestCase bound here), so that
# suite is not collected and set up a second time in this process.
import tests.test_business_day_offline_postgres as _bd

_now = _bd._now

PREFIX = "cauldra_offext"
SUPPLIER_EMAIL = "orders-desk@supplier.example"


class FakeResponse:
    def __init__(self, status, body=None):
        self.status_code = status
        self.ok = 200 <= status < 300
        self._body = body if body is not None else ({"id": "email_simulated"} if self.ok else {"name": "application_error", "message": "boom"})
        self.headers = {}

    def json(self):
        return self._body


class FakeProvider:
    """Stands in for Resend's HTTP API and records every call."""
    def __init__(self, *statuses):
        self.statuses = list(statuses) or [200]
        self.calls = []

    def __call__(self, url, headers=None, json=None, timeout=None):
        assert url == "https://api.resend.com/emails", url
        self.calls.append({"headers": dict(headers or {}), "json": json})
        status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        body = {"name": "validation_error", "message": "Invalid `to` field"} if status == 422 else None
        return FakeResponse(status, body)


@unittest.skipUnless(ADMIN_URL, "TEST_POSTGRES_ADMIN_URL is not configured")
class OfflineExternalPostgresTests(unittest.TestCase):
    _tenant = _bd.OfflineBusinessDayPostgresTests._tenant
    _captured = {}

    def _replay(self, tenant, role, op_type, payload, captured=None, op_id=None):
        # A retry resends the identical saved change, capture time included.
        op_id = op_id or str(uuid.uuid4())
        captured = self._captured.setdefault(op_id, captured or _now())
        return _bd.OfflineBusinessDayPostgresTests._replay(self, tenant, role, op_type, payload, captured, op_id)

    @classmethod
    def setUpClass(cls):
        cls.pg = create_postgres_test_schema(PREFIX, {"SUPPLY_AI_AUTO_CREATE_SCHEMA": "false"})
        cls.main = cls.pg.main
        from fastapi.testclient import TestClient
        cls.client = TestClient(cls.main.app)

    @classmethod
    def tearDownClass(cls):
        drop_postgres_test_schema(cls.pg, PREFIX)

    def setUp(self):
        # A configured sender with a placeholder key; the network call itself
        # is always the simulated provider.
        self._env = mock.patch.dict(os.environ, {"RESEND_API_KEY": "test-placeholder"})
        self._env.start()
        self._from = mock.patch.object(self.main, "RESEND_FROM", "Cauldra <orders@cauldra.example>")
        self._from.start()
        self.addCleanup(self._env.stop)
        self.addCleanup(self._from.stop)

    def _provider(self, *statuses):
        provider = FakeProvider(*statuses)
        patcher = mock.patch("requests.post", provider)
        patcher.start()
        self.addCleanup(patcher.stop)
        return provider

    # ---- helpers ------------------------------------------------------------
    def _db(self):
        return self.main.SessionLocal()

    def _draft(self, tenant, text="Please supply 10 x Rice 5kg."):
        m = self.main
        db = self._db()
        try:
            supplier = m.Supplier(name="Grain Co", contact_email=SUPPLIER_EMAIL, phone="08011111111", business_id=tenant["business_id"])
            db.add(supplier); db.flush()
            po = m.PurchaseOrder(supplier_id=supplier.id, status="DRAFT", total_estimated_cost=100.0, email_draft=text,
                                 business_id=tenant["business_id"], location_id=tenant["location_id"])
            db.add(po); db.commit()
            return {"po_id": po.id, "supplier_id": supplier.id,
                    "draft_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}
        finally:
            db.close()

    def _po(self, po_id):
        db = self._db()
        try:
            return db.get(self.main.PurchaseOrder, po_id)
        finally:
            db.close()

    def _code(self, response):
        detail = response.json().get("detail")
        return detail.get("code") if isinstance(detail, dict) else None

    # ---- tests --------------------------------------------------------------
    def test_queued_email_is_sent_once_with_a_provider_idempotency_key(self):
        tenant = self._tenant()
        payload = self._draft(tenant)
        provider = self._provider(200)
        op_id = str(uuid.uuid4())
        r = self._replay(tenant, "admin", "po_email_send", payload, op_id=op_id)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(provider.calls[0]["headers"].get("Idempotency-Key"), f"cauldra-offline-{op_id}")
        self.assertEqual(provider.calls[0]["json"]["to"], [SUPPLIER_EMAIL])
        po = self._po(payload["po_id"])
        self.assertEqual(po.status, "SENT")
        self.assertIsNotNone(po.sent_at)

        again = self._replay(tenant, "admin", "po_email_send", payload, op_id=op_id)
        self.assertEqual(again.status_code, 200, again.text)
        self.assertEqual(len(provider.calls), 1, "a retried change is answered from its receipt, never emailed twice")

        m = self.main
        db = self._db()
        try:
            self.assertEqual(db.query(m.AuditLog).filter_by(business_id=tenant["business_id"], action="PURCHASE_ORDER_DISPATCHED").count(), 1)
            self.assertEqual(db.query(m.Notification).filter_by(business_id=tenant["business_id"], type="PO_SUBMITTED").count() >= 1, True)
        finally:
            db.close()

    def test_provider_outage_is_retried_with_the_same_key_and_nothing_is_marked_sent(self):
        tenant = self._tenant()
        payload = self._draft(tenant)
        provider = self._provider(500, 200)
        op_id = str(uuid.uuid4())
        first = self._replay(tenant, "admin", "po_email_send", payload, op_id=op_id)
        self.assertEqual(first.status_code, 503, first.text)
        self.assertEqual(self._code(first), "PROVIDER_RETRY")
        self.assertEqual(self._po(payload["po_id"]).status, "DRAFT", "not sent until the provider accepts it")

        second = self._replay(tenant, "admin", "po_email_send", payload, op_id=op_id)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(self._po(payload["po_id"]).status, "SENT")
        keys = {call["headers"].get("Idempotency-Key") for call in provider.calls}
        self.assertEqual(keys, {f"cauldra-offline-{op_id}"}, "every attempt carries the same provider key")

    def test_unconfigured_provider_needs_attention_and_nothing_is_sent(self):
        tenant = self._tenant()
        payload = self._draft(tenant)
        provider = self._provider(200)
        with mock.patch.dict(os.environ, {"RESEND_API_KEY": ""}):
            r = self._replay(tenant, "admin", "po_email_send", payload)
        self.assertEqual(r.status_code, 409, r.text)
        self.assertEqual(self._code(r), "PROVIDER_UNAVAILABLE")
        self.assertEqual(provider.calls, [])
        self.assertEqual(self._po(payload["po_id"]).status, "DRAFT")

    def test_provider_rejection_needs_attention_without_echoing_the_address(self):
        tenant = self._tenant()
        payload = self._draft(tenant)
        self._provider(422)
        r = self._replay(tenant, "admin", "po_email_send", payload)
        self.assertEqual(r.status_code, 409, r.text)
        self.assertEqual(self._code(r), "PROVIDER_REJECTED")
        self.assertNotIn(SUPPLIER_EMAIL, r.text)
        self.assertNotIn("test-placeholder", r.text)
        self.assertEqual(self._po(payload["po_id"]).status, "DRAFT")

    def test_already_sent_order_is_not_sent_again(self):
        tenant = self._tenant()
        payload = self._draft(tenant)
        provider = self._provider(200)
        online = self.client.post(f"/purchase-orders/{payload['po_id']}/dispatch-email",
                                  headers={"Authorization": f"Bearer {tenant['users']['admin']['token']}"})
        self.assertEqual(online.status_code, 200, online.text)
        self.assertNotIn("Idempotency-Key", provider.calls[0]["headers"], "the online send is unchanged")
        r = self._replay(tenant, "admin", "po_email_send", payload)
        self.assertEqual(r.status_code, 409, r.text)
        self.assertEqual(self._code(r), "PO_ALREADY_SENT")
        self.assertEqual(len(provider.calls), 1)

    def test_changed_draft_or_supplier_is_not_sent(self):
        tenant = self._tenant()
        payload = self._draft(tenant)
        provider = self._provider(200)
        m = self.main
        db = self._db()
        try:
            db.get(m.PurchaseOrder, payload["po_id"]).email_draft = "Please supply 50 x Rice 5kg."
            db.commit()
        finally:
            db.close()
        r = self._replay(tenant, "admin", "po_email_send", payload)
        self.assertEqual(self._code(r), "PO_CHANGED", r.text)

        other = self._draft(tenant)
        r = self._replay(tenant, "admin", "po_email_send", {**other, "supplier_id": payload["supplier_id"]})
        self.assertEqual(self._code(r), "PO_CHANGED", r.text)
        self.assertEqual(provider.calls, [])

    def test_stale_email_is_not_sent_automatically(self):
        tenant = self._tenant(issued_hours_ago=24 * 5)
        payload = self._draft(tenant)
        provider = self._provider(200)
        r = self._replay(tenant, "admin", "po_email_send", payload, captured=_now() - timedelta(days=4))
        self.assertEqual(r.status_code, 409, r.text)
        self.assertEqual(self._code(r), "STALE_ACTION")
        self.assertEqual(provider.calls, [])
        self.assertEqual(self._po(payload["po_id"]).status, "DRAFT")

    def test_deleted_order_is_refused(self):
        tenant = self._tenant()
        payload = self._draft(tenant)
        provider = self._provider(200)
        m = self.main
        db = self._db()
        try:
            db.delete(db.get(m.PurchaseOrder, payload["po_id"])); db.commit()
        finally:
            db.close()
        r = self._replay(tenant, "admin", "po_email_send", payload)
        self.assertEqual(self._code(r), "RESOURCE_DELETED", r.text)
        self.assertEqual(provider.calls, [])

    def test_permission_is_rechecked_on_reconnect(self):
        tenant = self._tenant(overrides={"po.send": False})
        payload = self._draft(tenant)
        provider = self._provider(200)
        r = self._replay(tenant, "staff", "po_email_send", payload)
        # Refused by the same permission check as every replayed change; the
        # device shows a 403 as "your permissions changed".
        self.assertEqual(r.status_code, 403, r.text)
        self.assertEqual(provider.calls, [])
        self.assertEqual(self._po(payload["po_id"]).status, "DRAFT")

    def test_online_send_without_a_key_still_reports_unavailable(self):
        tenant = self._tenant()
        payload = self._draft(tenant)
        self._provider(200)
        with mock.patch.dict(os.environ, {"RESEND_API_KEY": ""}):
            r = self.client.post(f"/purchase-orders/{payload['po_id']}/dispatch-email",
                                 headers={"Authorization": f"Bearer {tenant['users']['admin']['token']}"})
        self.assertEqual(r.status_code, 503, r.text)
        self.assertEqual(r.json()["detail"], "Email sending isn't available right now. Please contact support.")


if __name__ == "__main__":
    unittest.main()
