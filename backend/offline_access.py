"""Offline device grants and atomic, authenticated replay. No offline bearer tokens.

Installed with install(globals()) by main after its domain routes are defined.
Domain handlers keep their existing validation; DeferredCommit keeps their writes
and the replay receipt in the SAME database transaction.
"""
import base64
import hashlib
import json
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from fastapi import Depends, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field, ValidationError
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey


def _utcnow():
    """Naive UTC for compatibility with the application's existing DB columns."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def b64(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def failure(code, message, status=409, details=None):
    detail = {"code": code, "message": message}
    if details is not None:
        detail["details"] = details
    raise HTTPException(status, detail)


class Provision(BaseModel):
    device_id: uuid.UUID


class Replay(BaseModel):
    schema_version: int
    op_id: uuid.UUID
    device_id: uuid.UUID
    user_id: int
    business_id: int
    auth_version: int
    type: str = Field(max_length=40)
    captured_at: datetime
    payload: dict


class DeferredCommit:
    def __init__(self, session):
        self.session = session

    def __getattr__(self, name):
        return getattr(self.session, name)

    def commit(self):
        self.session.flush()

    def rollback(self):
        # A domain uniqueness race must not roll back the outer receipt and
        # continue with an unclaimed mutation. The entire request is retried.
        failure("RETRY_TRANSACTION", "Concurrent change; retry this operation.", 503)


def install(g):
    Base, app = g["Base"], g["app"]

    class OfflineDevice(Base):
        __tablename__ = "offline_devices"
        device_id = Column(String(36), primary_key=True)
        business_id = Column(Integer, ForeignKey("business_profile.id", ondelete="CASCADE"), nullable=False, index=True)
        user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
        auth_version = Column(Integer, nullable=False)
        role = Column(String(40), nullable=False)
        permissions_hash = Column(String(64), nullable=False)
        issued_at = Column(DateTime, nullable=False)
        expires_at = Column(DateTime, nullable=False)
        revoked_at = Column(DateTime)

    g["OfflineDevice"] = OfflineDevice

    def permissions(user):
        return g["get_effective_permissions"](user)

    def permission_hash(user):
        return hashlib.sha256(json.dumps(permissions(user), sort_keys=True).encode()).hexdigest()

    def signing_key():
        pem = os.getenv("CAULDRA_OFFLINE_SIGNING_KEY", "").replace("\\n", "\n")
        if not pem:
            failure("NOT_CONFIGURED", "Offline device provisioning is not configured.", 503)
        try:
            key = serialization.load_pem_private_key(pem.encode(), password=None)
            if not isinstance(key, ec.EllipticCurvePrivateKey) or key.curve.name != "secp256r1":
                raise ValueError("P-256 required")
            return key
        except (ValueError, TypeError):
            failure("NOT_CONFIGURED", "Offline signing configuration is invalid.", 503)

    def device_for(db, user, device_id):
        row = db.query(OfflineDevice).filter_by(device_id=str(device_id)).with_for_update().first()
        if not row or row.business_id != user.business_id or row.user_id != user.id:
            failure("AUTH_EXPIRED", "Sign in as the original user on the original device.", 403)
        if row.revoked_at or row.auth_version != int(user.auth_version or 1):
            failure("AUTH_EXPIRED", "Offline device authorization has been revoked.", 403)
        if row.expires_at <= _utcnow():
            failure("AUTH_EXPIRED", "Offline device authorization has expired.", 403)
        if row.role != user.role or row.permissions_hash != permission_hash(user):
            failure("PERMISSION_CHANGED", "Permissions changed. Review preserved local work online.", 403)
        return row

    @app.post("/offline/provision")
    def provision(data: Provision, user=Depends(g["get_current_user"]), db=Depends(g["get_db"])):
        key = signing_key()
        now = _utcnow()
        days = max(1, min(7, int(os.getenv("CAULDRA_OFFLINE_DAYS", "7"))))
        row = db.query(OfflineDevice).filter_by(device_id=str(data.device_id)).with_for_update().first()
        if row and (row.user_id != user.id or row.business_id != user.business_id):
            failure("AUTH_EXPIRED", "This device identifier belongs to another workspace.", 403)
        if not row:
            row = OfflineDevice(device_id=str(data.device_id), user_id=user.id, business_id=user.business_id)
            db.add(row)
        row.auth_version = int(user.auth_version or 1)
        row.role = user.role
        row.permissions_hash = permission_hash(user)
        row.issued_at = now
        row.expires_at = now + timedelta(days=days)
        row.revoked_at = None
        grant = {"schema_version": 2, "device_id": row.device_id, "user_id": user.id,
                 "business_id": user.business_id, "auth_version": row.auth_version,
                 "role": user.role, "permissions": permissions(user),
                 "issued_at": int(time.time()), "last_server_verified": int(time.time()),
                 "expires_at": int(time.time()) + days * 86400, "unlock_required": True}
        payload = b64(json.dumps(grant, sort_keys=True, separators=(",", ":")).encode())
        r, s = decode_dss_signature(key.sign(payload.encode(), ec.ECDSA(hashes.SHA256())))
        signature = b64(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
        numbers = key.public_key().public_numbers()
        public_key = {"kty": "EC", "crv": "P-256", "x": b64(numbers.x.to_bytes(32, "big")),
                      "y": b64(numbers.y.to_bytes(32, "big")), "ext": True}
        db.commit()
        return {"payload": payload, "signature": signature, "public_key": public_key}

    @app.delete("/offline/devices/{device_id}")
    def revoke(device_id: uuid.UUID, user=Depends(g["get_authenticated_user"]), db=Depends(g["get_db"])):
        row = db.query(OfflineDevice).filter_by(device_id=str(device_id), user_id=user.id,
                                              business_id=user.business_id).with_for_update().first()
        if row:
            row.revoked_at = _utcnow()
            db.commit()
        return {"revoked": True}

    @app.get("/offline/snapshot")
    def snapshot(user=Depends(g["get_current_user"]), db=Depends(g["get_db"])):
        business = db.query(g["BusinessProfile"]).filter_by(id=user.business_id).first()
        # All catalog pages, not merely the first page of 500. Bound provisioning
        # explicitly; never label a truncated catalog complete.
        count = db.query(g["Product"]).filter_by(business_id=user.business_id).count()
        if count > 20000:
            failure("CATALOG_TOO_LARGE", "This catalog exceeds this device snapshot limit.", 413)
        products = []
        for offset in range(0, count, 500):
            products.extend(g["list_products"](500, offset, None, None, user, db))
        suppliers = []
        offset = 0
        while True:
            page = g["list_suppliers"](500, offset, user, db)
            suppliers.extend(page)
            if len(page) < 500:
                break
            offset += 500
            if offset >= 20000:
                failure("CATALOG_TOO_LARGE", "Supplier snapshot limit exceeded.", 413)
        stocks = [{"product_id": r.product_id, "warehouse_id": r.warehouse_id,
                   "quantity": r.quantity} for r in db.query(g["WarehouseStock"]).filter_by(business_id=user.business_id).all()]
        locations = [g["serialize_location"](r, db) for r in db.query(g["Location"]).filter_by(business_id=user.business_id, is_active=True).all()]
        days = [{"id": d.id, "location_id": d.location_id, "is_open": d.is_open}
                for d in db.query(g["BusinessDay"]).filter_by(business_id=user.business_id, is_open=True).all()]
        cache, freshness = {}, {}
        def cached(path, fn, *args):
            try:
                value = fn(*args)
                cache[path] = {"value": jsonable_encoder(value), "verified_at": _utcnow().isoformat()}
                freshness[path] = "synchronized"
                return value
            except HTTPException as exc:
                if exc.status_code != 403:
                    raise
                freshness[path] = "permission unavailable"
                return None
        for location in [None] + [r["id"] for r in locations]:
            suffix = f"?location_id={location}" if location is not None else ""
            cached("/sales/current-day" + suffix, g["current_sales_day"], location, user, db)
            cached("/business-days/current-summary" + suffix, g["current_business_day_summary"], location, user, db)
        since = (_utcnow() - timedelta(days=90)).date().isoformat()
        until = _utcnow().date().isoformat()
        expenses = []
        if permissions(user).get("expenses.view"):
            for offset in range(0, 20000, 500):
                page = g["list_expenses"](None, None, None, None, since, until, None, 500, offset, user, db)
                expenses.extend(page["expenses"])
                if len(expenses) >= page["total"]:
                    break
            if len(expenses) < page["total"]:
                failure("HISTORY_TOO_LARGE", "The 90-day expense history exceeds this device limit.", 413)
        history = cached("/sales/history?period=custom&custom_start=" + since + "&custom_end=" + until,
                         g["sales_history"], "custom", since, until, None, user, db)
        cached("/purchase-orders/", g["get_purchase_orders"], None, user, db)
        cached("/subscription/usage", g["subscription_usage"], user, db)
        cached("/business-brain", g["business_brain"], None, user, db)
        # Read existing notifications without running their condition-driven
        # write/dispatch helper during offline provisioning.
        notifications = db.query(g["Notification"]).filter_by(recipient_user_id=user.id).order_by(g["Notification"].id.desc()).limit(200).all()
        cache["/notifications"] = {"value": {"notifications": [g["serialize_notification"](n) for n in notifications],
            "unread_count": sum(not n.is_read for n in notifications)}, "verified_at": _utcnow().isoformat()}
        return jsonable_encoder({"schema_version": 2, "verified_at": _utcnow(),
            "user": {**g["serialize_user"](user), "business_id": user.business_id},
            "business": g["serialize_business"](business), "permissions": permissions(user),
            "products": products, "suppliers": suppliers, "stocks": stocks,
            "warehouses": g["list_warehouses"](user, db), "locations": locations,
            "days": days, "sales": [], "sales_history": history, "expenses": expenses,
            "cache": cache, "freshness": freshness, "history_since": since})

    @app.post("/offline/replay")
    def replay(op: Replay, request: Request, user=Depends(g["get_current_user"]), db=Depends(g["get_db"])):
        if op.schema_version != 2:
            failure("SCHEMA_CHANGED", "Update Cauldra before syncing this operation.", 400)
        if (op.business_id, op.user_id, op.auth_version) != (user.business_id, user.id, int(user.auth_version or 1)):
            failure("AUTH_EXPIRED", "The original identity no longer matches.", 403)
        device = device_for(db, user, op.device_id)
        captured = op.captured_at.replace(tzinfo=None)
        if captured < device.issued_at - timedelta(seconds=60) or captured > device.expires_at or captured > _utcnow() + timedelta(minutes=5):
            failure("AUTH_EXPIRED", "The operation was captured outside its authorized period.", 403)
        payload = dict(op.payload)
        ref = str(op.op_id)
        payload["client_ref"] = ref
        claim, existing = g["claim_idempotent_mutation"](db, user.business_id, "offline_v2", ref,
            jsonable_encoder(op.model_dump()))
        if existing:
            return existing
        rules = {"product_create": "inventory.add_product", "product_update": "inventory.edit_product",
                 "product_delete": "inventory.delete_product", "sale_checkout": "sales.create",
                 "expense_create": "expenses.record", "supplier_create": "supplier.create"}
        if op.type not in rules:
            failure("ONLINE_ONLY", "This operation requires an online workflow.", 400)
        g["require_permission"](user, rules[op.type])
        if op.type in ("sale_checkout", "expense_create"):
            day = db.query(g["BusinessDay"]).filter_by(id=payload.get("business_day_id"),
                business_id=user.business_id, location_id=payload.get("location_id")).with_for_update().first()
            if not day or not day.is_open:
                failure("BUSINESS_DAY_CLOSED", "The original Business Day is closed or unavailable.")
            location = db.query(g["Location"]).filter_by(id=payload.get("location_id"), business_id=user.business_id, is_active=True).first()
            if not location:
                failure("LOCATION_CHANGED", "The original location is unavailable.")
            business = db.query(g["BusinessProfile"]).filter_by(id=user.business_id).first()
            currency = g["normalize_currency_code"](location.currency or business.currency)
            if payload.get("currency") != currency:
                failure("LOCATION_CHANGED", "The original currency no longer matches.")
        if op.type == "product_create":
            warehouse = db.query(g["Warehouse"]).filter_by(name=payload.get("warehouse"), business_id=user.business_id, is_active=True).first()
            if not warehouse:
                failure("LOCATION_CHANGED", "The original warehouse is unavailable.")
        if op.type == "sale_checkout":
            for item in payload.get("items", []):
                warehouse = db.query(g["Warehouse"]).filter_by(id=item.get("warehouse_id"), business_id=user.business_id, is_active=True).first()
                if not warehouse or warehouse.location_id != payload.get("location_id"):
                    failure("LOCATION_CHANGED", "The original warehouse is unavailable at this location.")
                product = db.query(g["Product"]).filter_by(id=item.get("product_id"), business_id=user.business_id).with_for_update().first()
                if not product:
                    failure("RESOURCE_DELETED", "A product in this sale no longer exists.")
                stock = db.query(g["WarehouseStock"]).filter_by(
                    business_id=user.business_id, product_id=product.id, warehouse_id=warehouse.id
                ).with_for_update().first()
                requested = int(item.get("quantity") or 0)
                available = int(stock.quantity if stock else 0)
                if requested <= 0 or requested > available:
                    failure("STOCK_CHANGED", "Server stock is lower than the saved offline sale.", 409, {
                        "product_id": product.id, "product": product.name,
                        "warehouse_id": warehouse.id, "warehouse": warehouse.name,
                        "requested_quantity": requested, "available_quantity": available,
                        "sale_reference": ref, "captured_at": captured.isoformat(),
                    })
                if item.get("price_mode", "retail") != "negotiated":
                    price = product.wholesale_price if item.get("price_mode") == "wholesale" else product.retail_price
                    if item.get("unit_price") is None or round(float(item["unit_price"]), 2) != round(float(price or 0), 2):
                        failure("STOCK_CHANGED", "The catalog price changed. Review the original sale before retrying.")
        if op.type in ("product_update", "product_delete"):
            product = db.query(g["Product"]).filter_by(id=payload.get("id"), business_id=user.business_id).with_for_update().first()
            if not product:
                failure("RESOURCE_DELETED", "The original product no longer exists.")
            if not payload.get("base_updated_at") or g["to_utc_iso"](product.updated_at) != payload["base_updated_at"]:
                failure("STOCK_CHANGED", "Product changed after your local snapshot.", 409, {
                    "product_id": product.id, "base_updated_at": payload.get("base_updated_at"),
                    "server_updated_at": g["to_utc_iso"](product.updated_at),
                    "local_values": {k: v for k, v in payload.items() if k not in ("client_ref",)},
                    "server_values": {"name": product.name, "sku": product.sku, "category": product.category,
                                      "cost_price": product.cost_price, "wholesale_price": product.wholesale_price,
                                      "retail_price": product.retail_price, "min_stock_level": product.min_stock_level},
                })
            if op.type == "product_update" and any(k in payload for k in ("quantity", "warehouse")):
                failure("ONLINE_ONLY", "Stock and warehouse changes require the online stock workflow.", 400)
            if op.type == "product_delete" and user.role != "admin":
                failure("ONLINE_ONLY", "Deletion approval requires the online workflow.", 403)
        deferred = DeferredCommit(db)
        try:
            if op.type == "product_create":
                result = g["create_product"](g["ProductCreate"](**payload), request, user, deferred)
            elif op.type == "product_update":
                result = g["update_product"](payload["id"], g["ProductUpdate"](**payload), request, user, deferred)
            elif op.type == "product_delete":
                result = g["delete_product"](payload["id"], request, user, deferred)
            elif op.type == "sale_checkout":
                result = g["sales_checkout"](g["SalesCheckoutRequest"](**payload), request, user, deferred)
            elif op.type == "expense_create":
                result = g["create_expense"](g["ExpenseCreate"](**payload), request, user, deferred)
            else:
                result = g["create_supplier"](g["SupplierCreate"](**payload), user, deferred)
            response = {"op_id": ref, "status": "synced", "result": jsonable_encoder(result),
                        "captured_at": captured.isoformat(), "server_received_at": _utcnow().isoformat(),
                        "user_id": user.id, "business_id": user.business_id, "device_id": str(op.device_id)}
            g["complete_idempotent_mutation"](claim, response)
            db.commit()
            return response
        except ValidationError:
            db.rollback()
            failure("VALIDATION_ERROR", "The original operation contains invalid values.", 422)
        except HTTPException as exc:
            db.rollback()
            if isinstance(exc.detail, dict):
                raise
            code = {400: "VALIDATION_ERROR", 403: "PERMISSION_CHANGED", 404: "RESOURCE_DELETED", 409: "STOCK_CHANGED"}.get(exc.status_code, "VALIDATION_ERROR")
            failure(code, str(exc.detail), exc.status_code)

    return OfflineDevice
