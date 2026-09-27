"""OFFLINE-STOCK-REFUND-001 (G1 stock adjustments/transfers, G2 refunds) through
the real /offline/replay endpoint.

G1: an adjustment is a delta on one warehouse, applied once, on top of what
other devices did meanwhile, and refused (nothing changed) if it would make
stock negative. A transfer moves both rows or neither, and is refused whole if
the source no longer holds enough. Deleted products and deactivated warehouses
are refused; permissions are rechecked (and a changed grant is refused).

G2: a refund of a synchronized sale (by sale line) or of a sale that was itself
recorded offline (by cart line, after that sale synchronizes) runs the online
refund once, restocking once. Another till's refund of the same sale meanwhile
makes the offline refund refuse whole with the figures (REFUND_CONFLICT).
Refunds are internal to Cauldra: no payment provider is called.

Set TEST_POSTGRES_ADMIN_URL (see tests/postgres_test_support.py).
"""
from __future__ import annotations

import json
import unittest
import uuid
from datetime import timedelta

from tests.postgres_test_support import ADMIN_URL, create_postgres_test_schema, drop_postgres_test_schema
# Helpers are reached through the module (no TestCase bound here), so that
# suite is not collected and set up a second time in this process.
import tests.test_business_day_offline_postgres as _bd

_now = _bd._now

PREFIX = "cauldra_offsr"


@unittest.skipUnless(ADMIN_URL, "TEST_POSTGRES_ADMIN_URL is not configured")
class OfflineStockRefundPostgresTests(unittest.TestCase):
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

    # ---- helpers ------------------------------------------------------------
    def _db(self):
        return self.main.SessionLocal()

    def _second_warehouse(self, tenant, quantity=0):
        m = self.main
        db = self._db()
        try:
            wh = m.Warehouse(business_id=tenant["business_id"], name=f"Back Store {uuid.uuid4().hex[:4]}", is_active=True,
                             location_id=tenant["location_id"])
            db.add(wh); db.flush()
            if quantity:
                db.add(m.WarehouseStock(business_id=tenant["business_id"], product_id=tenant["product_id"], warehouse=wh.name,
                                        warehouse_id=wh.id, quantity=quantity))
                product = db.get(m.Product, tenant["product_id"]); product.quantity += quantity
            db.commit()
            return wh.id
        finally:
            db.close()

    def _stock(self, tenant, warehouse_id=None):
        m = self.main
        db = self._db()
        try:
            row = db.query(m.WarehouseStock).filter_by(product_id=tenant["product_id"], warehouse_id=warehouse_id or tenant["warehouse_id"]).first()
            product = db.get(m.Product, tenant["product_id"])
            return (int(row.quantity) if row else 0), (int(product.quantity) if product else None)
        finally:
            db.close()

    def _set_permissions(self, tenant, overrides):
        m = self.main
        db = self._db()
        try:
            staff = db.get(m.User, tenant["users"]["staff"]["id"])
            staff.permission_overrides = json.dumps(overrides)
            db.commit()
        finally:
            db.close()

    def _adjust(self, tenant, change, role="admin", op_id=None, warehouse_id=None):
        return self._replay(tenant, role, "stock_adjust", {"product_id": tenant["product_id"], "warehouse_id": warehouse_id or tenant["warehouse_id"],
                                                            "quantity_change": change, "reason": "Count correction", "base_quantity": 20}, op_id=op_id)

    def _transfer(self, tenant, to_id, quantity, role="admin", op_id=None):
        return self._replay(tenant, role, "stock_transfer", {"product_id": tenant["product_id"], "from_warehouse_id": tenant["warehouse_id"],
                                                              "to_warehouse_id": to_id, "quantity": quantity}, op_id=op_id)

    def _open_day(self, tenant):
        r = self._replay(tenant, "admin", "business_day_open", {"location_id": tenant["location_id"]})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["result"]["business_day_id"]

    def _sale(self, tenant, day_id, quantity=3, op_id=None):
        op_id = op_id or str(uuid.uuid4())
        r = self._replay(tenant, "admin", "sale_checkout", {
            "items": [{"product_id": tenant["product_id"], "quantity": quantity, "warehouse_id": tenant["warehouse_id"],
                       "price_mode": "retail", "unit_price": 20.0}],
            "location_id": tenant["location_id"], "business_day_id": day_id, "currency": "NGN"}, op_id=op_id)
        self.assertEqual(r.status_code, 200, r.text)
        m = self.main
        db = self._db()
        try:
            sale_id = db.query(m.SaleModel).filter_by(client_ref=op_id).one().id
        finally:
            db.close()
        return op_id, sale_id

    def _refund(self, tenant, day_id, key, lines, role="admin", op_id=None):
        return self._replay(tenant, role, "sale_refund", {"transaction_key": key, "lines": lines, "reason": "Customer return",
                                                           "business_day_id": day_id, "location_id": tenant["location_id"]}, op_id=op_id)

    def _refunded(self, tenant, sale_id):
        m = self.main
        db = self._db()
        try:
            return sum(int(l.quantity) for l in db.query(m.RefundLine).filter_by(original_sale_id=sale_id).all()), \
                db.query(m.RefundTransaction).filter_by(business_id=tenant["business_id"]).count()
        finally:
            db.close()

    # ---- G1: adjustments ----------------------------------------------------
    def test_adjustment_is_a_delta_applied_once_and_audited(self):
        tenant = self._tenant()
        op_id = str(uuid.uuid4())
        r = self._adjust(tenant, -3, op_id=op_id)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self._stock(tenant), (17, 17))
        again = self._adjust(tenant, -3, op_id=op_id)
        self.assertEqual(again.status_code, 200, again.text)
        self.assertEqual(self._stock(tenant), (17, 17), "a retried adjustment is answered, not applied twice")
        m = self.main
        db = self._db()
        try:
            audit = db.query(m.AuditLog).filter_by(business_id=tenant["business_id"], action="STOCK_ADJUSTED").one()
            meta = json.loads(audit.metadata_json)
            self.assertTrue(meta["offline"]); self.assertEqual(meta["offline_ref"], op_id)
            self.assertEqual(meta["quantity_change"], -3); self.assertEqual(meta["reason"], "Count correction")
        finally:
            db.close()

    def test_adjustment_applies_on_top_of_another_devices_change_and_never_goes_negative(self):
        tenant = self._tenant()
        # Another device sold/adjusted online meanwhile: 20 -> 5.
        online = self.client.patch(f"/products/{tenant['product_id']}/stock", json={"quantity_change": -15},
                                   headers={"Authorization": f"Bearer {tenant['users']['admin']['token']}"})
        self.assertEqual(online.status_code, 200, online.text)
        ok = self._adjust(tenant, -4)
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertEqual(self._stock(tenant), (1, 1), "the offline delta lands on the current figure, never overwrites it")
        refused = self._adjust(tenant, -2)
        self.assertEqual(refused.status_code, 409, refused.text)
        detail = refused.json()["detail"]
        self.assertEqual(detail["code"], "STOCK_CHANGED")
        self.assertEqual(detail["details"]["available_quantity"], 1)
        self.assertIn("negative", detail["message"])
        self.assertEqual(self._stock(tenant), (1, 1), "nothing changed")

    def test_adjustment_permission_is_rechecked(self):
        tenant = self._tenant()
        denied = self._adjust(tenant, 1, role="staff")
        self.assertEqual(denied.status_code, 403, denied.text)
        self.assertEqual(self._stock(tenant), (20, 20))
        granted = self._tenant(overrides={"inventory.adjust_stock": True})
        self.assertEqual(self._adjust(granted, 1, role="staff").status_code, 200)
        self._set_permissions(granted, {})  # revoked before the next reconnect
        changed = self._adjust(granted, 1, role="staff")
        self.assertEqual(changed.status_code, 403, changed.text)
        self.assertEqual(changed.json()["detail"]["code"], "PERMISSION_CHANGED")
        self.assertEqual(self._stock(granted), (21, 21))

    def test_adjustment_for_deleted_product_or_inactive_warehouse_is_refused(self):
        tenant = self._tenant()
        m = self.main
        db = self._db()
        try:
            db.get(m.Warehouse, tenant["warehouse_id"]).is_active = False; db.commit()
        finally:
            db.close()
        inactive = self._adjust(tenant, 2)
        self.assertEqual(inactive.status_code, 409, inactive.text)
        self.assertEqual(inactive.json()["detail"]["code"], "LOCATION_CHANGED")
        self.assertEqual(self._stock(tenant), (20, 20))
        other = self._tenant()
        db = self._db()
        try:
            db.query(m.WarehouseStock).filter_by(product_id=other["product_id"]).delete()
            db.delete(db.get(m.Product, other["product_id"])); db.commit()
        finally:
            db.close()
        gone = self._adjust(other, 2)
        self.assertEqual(gone.status_code, 409, gone.text)
        self.assertEqual(gone.json()["detail"]["code"], "RESOURCE_DELETED")

    # ---- G1: transfers ------------------------------------------------------
    def test_transfer_moves_both_rows_once(self):
        tenant = self._tenant()
        back = self._second_warehouse(tenant)
        op_id = str(uuid.uuid4())
        r = self._transfer(tenant, back, 8, op_id=op_id)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self._stock(tenant), (12, 20))
        self.assertEqual(self._stock(tenant, back)[0], 8)
        again = self._transfer(tenant, back, 8, op_id=op_id)
        self.assertEqual(again.status_code, 200, again.text)
        self.assertEqual((self._stock(tenant)[0], self._stock(tenant, back)[0]), (12, 8), "a retried transfer is not moved twice")
        m = self.main
        db = self._db()
        try:
            meta = json.loads(db.query(m.AuditLog).filter_by(business_id=tenant["business_id"], action="STOCK_TRANSFER").one().metadata_json)
            self.assertTrue(meta["offline"]); self.assertEqual(meta["quantity"], 8)
        finally:
            db.close()

    def test_transfer_with_too_little_source_stock_moves_nothing(self):
        tenant = self._tenant()
        back = self._second_warehouse(tenant, quantity=4)
        # Another device took stock from the source meanwhile: 20 -> 6.
        self.client.patch(f"/products/{tenant['product_id']}/stock", json={"quantity_change": -14},
                          headers={"Authorization": f"Bearer {tenant['users']['admin']['token']}"})
        r = self._transfer(tenant, back, 10)
        self.assertEqual(r.status_code, 409, r.text)
        detail = r.json()["detail"]
        self.assertEqual(detail["code"], "STOCK_CHANGED")
        self.assertEqual((detail["details"]["available_quantity"], detail["details"]["requested_quantity"]), (6, 10))
        self.assertEqual(self._stock(tenant), (6, 10), "source unchanged, product total unchanged")
        self.assertEqual(self._stock(tenant, back)[0], 4, "destination unchanged: no partial transfer")

    def test_transfer_to_inactive_warehouse_or_without_permission_moves_nothing(self):
        tenant = self._tenant()
        back = self._second_warehouse(tenant)
        denied = self._transfer(tenant, back, 2, role="staff")
        self.assertEqual(denied.status_code, 403, denied.text)
        m = self.main
        db = self._db()
        try:
            db.get(m.Warehouse, back).is_active = False; db.commit()
        finally:
            db.close()
        r = self._transfer(tenant, back, 2)
        self.assertEqual(r.status_code, 409, r.text)
        self.assertEqual(r.json()["detail"]["code"], "LOCATION_CHANGED")
        self.assertEqual(self._stock(tenant), (20, 20))

    # ---- G2: refunds --------------------------------------------------------
    def test_refund_of_synced_sale_restocks_once_and_retry_is_idempotent(self):
        tenant = self._tenant()
        day = self._open_day(tenant)
        key, sale_id = self._sale(tenant, day, quantity=3)
        self.assertEqual(self._stock(tenant), (17, 17))
        op_id = str(uuid.uuid4())
        r = self._refund(tenant, day, key, [{"sale_id": sale_id, "product_id": tenant["product_id"], "quantity": 2, "restock": True}], op_id=op_id)
        self.assertEqual(r.status_code, 200, r.text)
        result = r.json()["result"]
        self.assertEqual(result["refund_total"], 40.0)
        self.assertEqual(result["business_day_id"], day)
        self.assertEqual(self._stock(tenant), (19, 19))
        again = self._refund(tenant, day, key, [{"sale_id": sale_id, "product_id": tenant["product_id"], "quantity": 2, "restock": True}], op_id=op_id)
        self.assertEqual(again.status_code, 200, again.text)
        self.assertEqual(self._refunded(tenant, sale_id), (2, 1), "one refund, not two")
        self.assertEqual(self._stock(tenant), (19, 19), "stock restored exactly once")
        m = self.main
        db = self._db()
        try:
            refund = db.query(m.RefundTransaction).filter_by(business_id=tenant["business_id"]).one()
            self.assertEqual(refund.client_ref, op_id)
        finally:
            db.close()

    def test_refund_of_offline_sale_by_cart_line_after_the_sale_syncs(self):
        tenant = self._tenant()
        day = self._open_day(tenant)
        sale_op = str(uuid.uuid4())
        # The client queues the refund with a dependency on the sale; if it
        # were ever sent first, it is refused and nothing is written.
        early = self._refund(tenant, day, sale_op, [{"item_index": 0, "product_id": tenant["product_id"], "quantity": 1, "restock": False}])
        self.assertEqual(early.status_code, 409, early.text)
        self.assertEqual(early.json()["detail"]["code"], "RESOURCE_DELETED")
        _, sale_id = self._sale(tenant, day, quantity=3, op_id=sale_op)
        r = self._refund(tenant, day, sale_op, [{"item_index": 0, "product_id": tenant["product_id"], "quantity": 1, "restock": False}])
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self._refunded(tenant, sale_id), (1, 1))
        self.assertEqual(self._stock(tenant), (17, 17), "no restock was asked for")
        wrong = self._refund(tenant, day, sale_op, [{"item_index": 0, "product_id": tenant["product_id"] + 999, "quantity": 1}])
        self.assertEqual(wrong.status_code, 422, wrong.text)

    def test_partial_refund_elsewhere_refuses_the_offline_remainder_whole(self):
        tenant = self._tenant()
        day = self._open_day(tenant)
        key, sale_id = self._sale(tenant, day, quantity=3)
        online = self.client.post(f"/sales/transactions/{key}/refund", json={"lines": [{"sale_id": sale_id, "quantity": 2, "restock": True}]},
                                  headers={"Authorization": f"Bearer {tenant['users']['admin']['token']}"})
        self.assertEqual(online.status_code, 200, online.text)
        r = self._refund(tenant, day, key, [{"sale_id": sale_id, "product_id": tenant["product_id"], "quantity": 2, "restock": True}])
        self.assertEqual(r.status_code, 409, r.text)
        detail = r.json()["detail"]
        self.assertEqual(detail["code"], "REFUND_CONFLICT")
        self.assertEqual((detail["details"]["remaining_quantity"], detail["details"]["already_refunded"]), (1, 2))
        self.assertIn("Nothing from this refund was applied", detail["message"])
        self.assertEqual(self._refunded(tenant, sale_id), (2, 1))
        self.assertEqual(self._stock(tenant), (19, 19), "stock restored only by the refund that happened")

    def test_full_refund_elsewhere_and_over_quantity_are_refused(self):
        tenant = self._tenant()
        day = self._open_day(tenant)
        key, sale_id = self._sale(tenant, day, quantity=2)
        over = self._refund(tenant, day, key, [{"sale_id": sale_id, "product_id": tenant["product_id"], "quantity": 3}])
        self.assertEqual(over.status_code, 409, over.text)
        self.assertEqual(over.json()["detail"]["code"], "REFUND_CONFLICT")
        full = self._refund(tenant, day, key, [{"sale_id": sale_id, "product_id": tenant["product_id"], "quantity": 2}])
        self.assertEqual(full.status_code, 200, full.text)
        second_device = self._refund(tenant, day, key, [{"sale_id": sale_id, "product_id": tenant["product_id"], "quantity": 1}])
        self.assertEqual(second_device.status_code, 409, second_device.text)
        self.assertEqual(second_device.json()["detail"]["details"]["remaining_quantity"], 0)
        self.assertEqual(self._refunded(tenant, sale_id), (2, 1))
        self.assertEqual(self._stock(tenant), (20, 20))

    def test_refund_permission_is_rechecked(self):
        tenant = self._tenant()
        day = self._open_day(tenant)
        key, sale_id = self._sale(tenant, day)
        line = [{"sale_id": sale_id, "product_id": tenant["product_id"], "quantity": 1}]
        denied = self._refund(tenant, day, key, line, role="staff")
        self.assertEqual(denied.status_code, 403, denied.text)
        granted = self._tenant(overrides={"sales.refund": True, "sales.create": True})
        gday = self._open_day(granted)
        gkey, gsale = self._sale(granted, gday)
        self._set_permissions(granted, {"sales.create": True})
        changed = self._refund(granted, gday, gkey, [{"sale_id": gsale, "product_id": granted["product_id"], "quantity": 1}], role="staff")
        self.assertEqual(changed.status_code, 403, changed.text)
        self.assertEqual(changed.json()["detail"]["code"], "PERMISSION_CHANGED")
        self.assertEqual(self._refunded(granted, gsale), (0, 0))

    def test_refund_needs_its_open_day_and_a_close_waits_for_it(self):
        tenant = self._tenant()
        day = self._open_day(tenant)
        key, sale_id = self._sale(tenant, day)
        refund_op = str(uuid.uuid4())
        r = self._refund(tenant, day, key, [{"sale_id": sale_id, "product_id": tenant["product_id"], "quantity": 1}], op_id=refund_op)
        self.assertEqual(r.status_code, 200, r.text)
        close = self._replay(tenant, "admin", "business_day_close", {"business_day_id": day, "location_id": tenant["location_id"],
                                                                    "own_refs": [key, refund_op]}, captured=_now() - timedelta(seconds=1))
        self.assertEqual(close.status_code, 200, close.text)
        late = self._refund(tenant, day, key, [{"sale_id": sale_id, "product_id": tenant["product_id"], "quantity": 1}])
        self.assertEqual(late.status_code, 409, late.text)
        self.assertEqual(late.json()["detail"]["code"], "BUSINESS_DAY_CLOSED")

    def test_refund_auto_opens_day_with_refund_permission(self):
        tenant = self._tenant(overrides={"sales.refund": True})
        auto = self._replay(tenant, "staff", "business_day_open", {"location_id": tenant["location_id"], "auto": True, "trigger": "refund"})
        self.assertEqual(auto.status_code, 200, auto.text)

    def test_snapshot_carries_refundable_sales_only_with_refund_permission(self):
        tenant = self._tenant()
        day = self._open_day(tenant)
        key, sale_id = self._sale(tenant, day, quantity=3)
        headers = {"Authorization": f"Bearer {tenant['users']['admin']['token']}"}
        self.client.post(f"/sales/transactions/{key}/refund", json={"lines": [{"sale_id": sale_id, "quantity": 1}]}, headers=headers)
        snap = self.client.get("/offline/snapshot", headers=headers)
        self.assertEqual(snap.status_code, 200, snap.text)
        txns = {t["transaction_key"]: t for t in snap.json()["refundable_sales"]}
        self.assertEqual(txns[key]["items"][0]["available_quantity"], 2)
        self.assertEqual(txns[key]["location_id"], tenant["location_id"])
        staff = self.client.get("/offline/snapshot", headers={"Authorization": f"Bearer {tenant['users']['staff']['token']}"})
        self.assertEqual(staff.status_code, 200, staff.text)
        self.assertEqual(staff.json()["refundable_sales"], [])
        self.assertEqual(staff.json()["freshness"]["refundable_sales"], "permission unavailable")

    def test_refund_path_calls_no_payment_provider(self):
        import inspect
        source = inspect.getsource(self.main.create_refund)
        self.assertNotIn("paystack", source.lower(), "sale refunds are internal: no provider money movement to fake offline")


if __name__ == "__main__":
    unittest.main()
