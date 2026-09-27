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


def validation_reason(exc):
    """OFFLINE-QUEUE-001: which saved value was refused and why, instead of one
    generic sentence. Field paths and pydantic's problem text only - never the
    submitted values themselves."""
    fields = []
    for error in exc.errors(include_url=False, include_input=False, include_context=False):
        path = ".".join(str(part) for part in error.get("loc", ()))
        problem = str(error.get("msg") or "is not valid").removeprefix("Value error, ")
        fields.append({"field": path, "problem": problem})
    if not fields:
        return "The saved offline change contains a value the server does not accept.", fields
    name = next((str(part) for part in reversed(fields[0]["field"].split(".")) if not part.isdigit()), "value")
    message = f"The saved offline change has an invalid {name.replace('_', ' ')}: {fields[0]['problem']}."
    if len(fields) > 1:
        message += f" ({len(fields) - 1} more value{'s' if len(fields) > 2 else ''} also need review.)"
    return message, fields


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
        # X2 — same rule as GET /products/ (inventory.view OR sales.create);
        # without either, the catalog and its stock are simply not provisioned.
        catalog_permitted = g["can_read_product_catalog"](user)
        products = []
        if catalog_permitted:
            for offset in range(0, count, 500):
                products.extend(g["list_products"](500, offset, None, None, user, db))
        # PERM-001 O1 — the snapshot is a SECOND read path to the same data as
        # GET /suppliers/, so it enforces the same permission. It must not
        # simply call through and let the 403 escape: these calls are outside
        # the cached() wrapper below, so an escaping 403 would fail the whole
        # provisioning for a Staff member who is legitimately allowed to work
        # offline. Denied => empty list plus a freshness marker, exactly like
        # the expenses block already does.
        suppliers_permitted = bool(permissions(user).get("supplier.view"))
        suppliers = []
        if suppliers_permitted:
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
                   "quantity": r.quantity} for r in db.query(g["WarehouseStock"]).filter_by(business_id=user.business_id).all()] if catalog_permitted else []
        locations = [g["serialize_location"](r, db) for r in db.query(g["Location"]).filter_by(business_id=user.business_id, is_active=True).all()]
        days = [{"id": d.id, "location_id": d.location_id, "is_open": d.is_open}
                for d in db.query(g["BusinessDay"]).filter_by(business_id=user.business_id, is_open=True).all()]
        cache, freshness = {}, {}
        if not suppliers_permitted:
            freshness["/suppliers/"] = "permission unavailable"
        if not catalog_permitted:
            freshness["/products/"] = "permission unavailable"
        # PERM-001 O2 — warehouses follow the same rule as their endpoints:
        # the full management listing only with warehouse.view, otherwise the
        # minimal operational projection (id/name/location_id) that offline
        # POS and offline product-create actually need, and nothing at all if
        # the device's user has neither operational permission.
        if permissions(user).get("warehouse.view"):
            warehouses = g["list_warehouses"](user, db)
        elif permissions(user).get("inventory.view") or permissions(user).get("sales.create"):
            warehouses = g["operational_warehouse_projection"](db, user.business_id)
            freshness["/warehouses/"] = "operational projection"
        else:
            warehouses = []
            freshness["/warehouses/"] = "permission unavailable"
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
        # G2: the device's trusted list of recent synchronized sales it may
        # refund offline, with what is still refundable on each line. Bounded
        # by the refund list's own 500-line cap; when the cap was reached, the
        # oldest transaction may be incomplete, so it is left out (a sale not
        # on this list needs the internet to refund).
        refundable = []
        if permissions(user).get("sales.refund"):
            refundable = g["list_sale_transactions"](None, "custom", since, until, user, db) or []
            if sum(len(t["items"]) for t in refundable) >= 500 and refundable:
                refundable = refundable[:-1]
                freshness["refundable_sales"] = "most recent sales only"
            else:
                freshness["refundable_sales"] = "synchronized"
        else:
            freshness["refundable_sales"] = "permission unavailable"
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
            "business": g["serialize_business"](business, db), "permissions": permissions(user),
            "products": products, "suppliers": suppliers, "stocks": stocks,
            "warehouses": warehouses, "locations": locations,
            "days": days, "sales": [], "refundable_sales": refundable, "sales_history": history, "expenses": expenses,
            "cache": cache, "freshness": freshness, "history_since": since})

    def open_business_day(db, user, payload, captured, ref):
        """BUSINESS-DAY-OFFLINE-001: a Business Day opened on a device while it
        was offline. Same permission as the online Open Business Day button
        (enforced by the caller's rule table) and the same one-open-day-per-
        location rule. If this location already has an open day from the SAME
        location-local date (another device opened it, or this device's
        snapshot was stale), the offline day is joined to it - the sales it
        recorded belong to that session, exactly as an online sale joins the
        open day. An open day from a different date is never joined: the
        change is refused so the sales waiting on it stay on the device for
        review instead of landing in an unrelated day."""
        location = db.query(g["Location"]).filter_by(id=payload.get("location_id"), business_id=user.business_id, is_active=True).first()
        if not location:
            failure("LOCATION_CHANGED", "The original location is unavailable.")
        business = db.query(g["BusinessProfile"]).filter_by(id=user.business_id).with_for_update().one()
        opened_at = min(captured, _utcnow())
        local_date = opened_at.replace(tzinfo=timezone.utc).astimezone(g["resolve_period_zoneinfo"](business, location)).date().isoformat()
        active = g["get_active_business_day"](db, user.business_id, location_id=location.id)
        auto = bool(payload.get("auto"))
        if active:
            if active.date != local_date:
                failure("BUSINESS_DAY_CONFLICT", "A Business Day from a different date is already open at this location. "
                        "The work saved offline was kept on this device and was not added to that day.", 409,
                        {"location_id": location.id, "offline_date": local_date, "open_day_date": active.date})
            g["add_audit"](db, user, "BUSINESS_DAY_OFFLINE_OPEN_JOINED",
                           f"A Business Day opened offline on {local_date} was joined to the Business Day already open at this location.",
                           business_id=user.business_id, business_day_id=active.id, location_id=location.id,
                           metadata={"offline_opened_at": opened_at.isoformat(), "offline_ref": ref, "auto": auto})
            day, joined = active, True
        else:
            day = g["_create_business_day_session"](db, user.business_id, user, auto=auto, commit=False, location_id=location.id,
                                                    opened_at=opened_at, audit_extra={"offline": True, "offline_ref": ref})
            joined = False
        return {"business_day_id": day.id, "joined_existing": joined, "business_day": g["serialize_business_day"](day, db)}

    def close_business_day(db, user, payload, captured, ref):
        """BUSINESS-DAY-OFFLINE-001: a Business Day closed on a device while
        offline. The device sends it only after the day's own offline work
        (its sales/expenses depend-on list), and it names that work in
        own_refs. Closes exactly that day by id, never "whatever is open now",
        with the moment it was really closed.
        - already closed on the server (another device closed it): nothing to
          change; answered and audited, never reopened or closed twice;
        - someone else recorded work in the day after it was closed offline:
          closing it at the offline time would put that work after the close,
          so it is refused (BUSINESS_DAY_CONFLICT) and the day is left open."""
        day = db.query(g["BusinessDay"]).filter_by(id=payload.get("business_day_id"), business_id=user.business_id).with_for_update().first()
        if not day or (payload.get("location_id") is not None and day.location_id != payload.get("location_id")):
            failure("RESOURCE_DELETED", "The Business Day closed offline no longer exists at this location.")
        closed_at = max(day.opened_at, min(captured, _utcnow()))
        if not day.is_open:
            g["add_audit"](db, user, "BUSINESS_DAY_OFFLINE_CLOSE_ALREADY_CLOSED",
                           f"Business day {day.date} was closed offline on this device; it was already closed on the server.",
                           business_id=user.business_id, business_day_id=day.id, location_id=day.location_id,
                           metadata={"offline_closed_at": closed_at.isoformat(), "offline_ref": ref,
                                     "server_closed_at": day.closed_at.isoformat() if day.closed_at else None})
            return {"business_day_id": day.id, "already_closed": True, "business_day": g["serialize_business_day"](day, db)}
        own = [str(r) for r in (payload.get("own_refs") or [])][:5000]
        Sale, Expense, Refund = g["SaleModel"], g["Expense"], g["RefundTransaction"]
        def others(model, when):
            q = db.query(model).filter(model.business_day_id == day.id, when > closed_at)
            if hasattr(model, "client_ref") and own:
                q = q.filter((model.client_ref.is_(None)) | (~model.client_ref.in_(own)))
            return q.count()
        later = others(Sale, Sale.timestamp) + others(Expense, Expense.created_at) + others(Refund, Refund.created_at)
        if later:
            failure("BUSINESS_DAY_CONFLICT", "Other work was recorded in this Business Day after it was closed offline, "
                    "so it was left open. Check it and close it online.", 409,
                    {"business_day_id": day.id, "records_after_offline_close": later})
        result = g["_close_business_day"](DeferredCommit(db), day, user, closed_at=closed_at,
                                          audit_extra={"offline": True, "offline_ref": ref, "offline_closed_at": closed_at.isoformat()})
        return {"business_day_id": day.id, "already_closed": False, "business_day": result["business_day"]}

    # ---- G1/G2: stock and refunds recorded offline (OFFLINE-STOCK-REFUND-001) ----
    def active_warehouse(db, user, warehouse_id):
        warehouse = db.query(g["Warehouse"]).filter_by(id=warehouse_id, business_id=user.business_id, is_active=True).first()
        if warehouse and warehouse.location_id is not None:
            if not db.query(g["Location"]).filter_by(id=warehouse.location_id, business_id=user.business_id, is_active=True).first():
                warehouse = None
        return warehouse

    def locked_stock(db, user, product, warehouse):
        WS = g["WarehouseStock"]
        return db.query(WS).filter(WS.business_id == user.business_id, WS.product_id == product.id,
            (WS.warehouse_id == warehouse.id) | (WS.warehouse_id.is_(None) & (WS.warehouse == warehouse.name))
        ).order_by(WS.id).with_for_update().first()

    def locked_product(db, user, product_id, what):
        product = db.query(g["Product"]).filter_by(id=product_id, business_id=user.business_id).with_for_update().first()
        if not product:
            failure("RESOURCE_DELETED", f"The product in this {what} no longer exists. Nothing was changed.")
        return product

    def adjust_stock(db, user, payload, captured, ref):
        """A stock adjustment recorded offline: a DELTA on one named warehouse,
        exactly like the online +/- adjustment. Applied on top of whatever
        other devices did meanwhile (never an absolute overwrite); refused
        with the current figure if it would make stock negative."""
        change = int(payload.get("quantity_change") or 0)
        if change == 0:
            failure("VALIDATION_ERROR", "The saved stock adjustment has no quantity.", 422)
        product = locked_product(db, user, payload.get("product_id"), "stock adjustment")
        warehouse = active_warehouse(db, user, payload.get("warehouse_id"))
        if not warehouse:
            failure("LOCATION_CHANGED", "The warehouse this stock adjustment was recorded for is no longer active. Nothing was changed.")
        stock = locked_stock(db, user, product, warehouse)
        available = int(stock.quantity if stock else 0)
        if available + change < 0:
            failure("STOCK_CHANGED", f"{product.name} now has {available} in {warehouse.name}, so removing {-change} would make stock negative. "
                    "Nothing was changed; count the stock and adjust it online.", 409,
                    {"product_id": product.id, "product": product.name, "warehouse_id": warehouse.id, "warehouse": warehouse.name,
                     "available_quantity": available, "quantity_change": change, "quantity_seen_offline": payload.get("base_quantity")})
        if not stock:
            stock = g["WarehouseStock"](business_id=user.business_id, product_id=product.id, warehouse=warehouse.name, warehouse_id=warehouse.id, quantity=0)
            db.add(stock); db.flush()
        elif stock.warehouse_id is None:
            stock.warehouse_id = warehouse.id
        before = int(stock.quantity)
        g["_apply_stock_adjustment"](db, user, product, stock, warehouse.name, change, audit_extra={
            "offline": True, "offline_ref": ref, "offline_captured_at": captured.isoformat(),
            "reason": (payload.get("reason") or None), "warehouse_id": warehouse.id,
            "quantity_seen_offline": payload.get("base_quantity"), "warehouse_quantity_before": before})
        return {"product_id": product.id, "warehouse_id": warehouse.id, "quantity_change": change,
                "warehouse_quantity": int(stock.quantity), "quantity": product.quantity}

    def transfer_stock(db, user, payload, captured, ref):
        """A stock transfer recorded offline: both warehouse rows move in this
        one transaction or neither does; refused whole if the source no longer
        holds enough."""
        quantity = int(payload.get("quantity") or 0)
        if quantity < 1 or payload.get("from_warehouse_id") == payload.get("to_warehouse_id"):
            failure("VALIDATION_ERROR", "The saved transfer needs two different warehouses and a positive quantity.", 422)
        product = locked_product(db, user, payload.get("product_id"), "transfer")
        source_wh = active_warehouse(db, user, payload.get("from_warehouse_id"))
        target_wh = active_warehouse(db, user, payload.get("to_warehouse_id"))
        if not source_wh or not target_wh:
            failure("LOCATION_CHANGED", "A warehouse in this transfer is no longer active. Nothing was moved.")
        rows = {}
        for wh in sorted((source_wh, target_wh), key=lambda w: w.id):  # fixed lock order
            rows[wh.id] = locked_stock(db, user, product, wh)
        source, target = rows[source_wh.id], rows[target_wh.id]
        available = int(source.quantity if source else 0)
        if available < quantity:
            failure("STOCK_CHANGED", f"{source_wh.name} now has only {available} of {product.name}, so {quantity} could not be transferred. "
                    "Nothing was moved.", 409,
                    {"product_id": product.id, "product": product.name, "from_warehouse": source_wh.name, "to_warehouse": target_wh.name,
                     "available_quantity": available, "requested_quantity": quantity})
        if source.warehouse_id is None:
            source.warehouse_id = source_wh.id
        if not target:
            target = g["WarehouseStock"](business_id=user.business_id, product_id=product.id, warehouse=target_wh.name, warehouse_id=target_wh.id, quantity=0)
            db.add(target); db.flush()
        elif target.warehouse_id is None:
            target.warehouse_id = target_wh.id
        g["_apply_stock_transfer"](db, user, product, source, target, source_wh, target_wh, quantity, audit_extra={
            "offline": True, "offline_ref": ref, "offline_captured_at": captured.isoformat(), "reason": (payload.get("reason") or None)})
        return {"product_id": product.id, "quantity_transferred": quantity, "from_warehouse_id": source_wh.id,
                "to_warehouse_id": target_wh.id, "from_quantity": int(source.quantity), "to_quantity": int(target.quantity),
                "quantity": product.quantity}

    def refund_sale(db, user, payload, captured, ref, request):
        """A refund recorded offline. Cauldra sale refunds are internal
        (accounting plus optional restock; no payment provider is involved),
        so the online refund itself runs here with the op id as its
        idempotency key. Before that, with the sale rows locked, every line is
        checked against what is STILL refundable: if another till refunded
        some or all of it meanwhile, the whole offline refund is refused with
        the figures, never partly applied or applied twice."""
        Sale = g["SaleModel"]
        key = str(payload.get("transaction_key") or "")
        sales = g["_sales_for_transaction_key"](db, user.business_id, key) if key else []
        if not sales:
            failure("RESOURCE_DELETED", "The sale this refund is for was not found on the server. Nothing was refunded.")
        sales = db.query(Sale).filter(Sale.id.in_([s.id for s in sales])).order_by(Sale.id).with_for_update().all()
        day_ids = {s.business_day_id for s in sales if s.business_day_id}
        original_day = db.query(g["BusinessDay"]).filter_by(id=next(iter(day_ids))).first() if len(day_ids) == 1 else None
        if not original_day or original_day.location_id != payload.get("location_id"):
            failure("LOCATION_CHANGED", "The original sale's location could not be confirmed, so this refund must be done online.")
        by_id = {s.id: s for s in sales}
        lines = []
        for line in payload.get("lines") or []:
            if line.get("sale_id") is not None:
                sale = by_id.get(line.get("sale_id"))
            else:
                index = line.get("item_index")
                sale = sales[index] if isinstance(index, int) and 0 <= index < len(sales) else None
            if not sale or (line.get("product_id") is not None and sale.product_id != line.get("product_id")):
                failure("VALIDATION_ERROR", "An item in this refund no longer matches the original sale. Nothing was refunded.", 422)
            quantity = int(line.get("quantity") or 0)
            already = db.query(g["func"].coalesce(g["func"].sum(g["RefundLine"].quantity), 0)).filter(
                g["RefundLine"].original_sale_id == sale.id, g["RefundLine"].business_id == user.business_id).scalar() or 0
            remaining = sale.quantity - int(already) - sum(l["quantity"] for l in lines if l["sale_id"] == sale.id)
            name = sale.product_name_snapshot or "an item"
            if quantity < 1 or quantity > remaining:
                failure("REFUND_CONFLICT", f"This refund asks for {quantity} of {name}, but only {max(0, remaining)} of the {sale.quantity} sold "
                        f"can still be refunded ({int(already)} already refunded). Nothing from this refund was applied.", 409,
                        {"sale_id": sale.id, "product": name, "sold_quantity": sale.quantity, "already_refunded": int(already),
                         "remaining_quantity": max(0, remaining), "requested_quantity": quantity})
            lines.append({"sale_id": sale.id, "quantity": quantity, "restock": bool(line.get("restock", True))})
        if not lines:
            failure("VALIDATION_ERROR", "The saved refund has no items.", 422)
        body = g["RefundRequest"](lines=lines, reason=payload.get("reason"), note=payload.get("note"), client_ref=ref)
        result = g["create_refund"](key, body, user, DeferredCommit(db))
        return {**result, "transaction_key": key, "offline": True}

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
                 "expense_create": "expenses.record", "supplier_create": "supplier.create",
                 "business_day_open": "business_day.manage", "business_day_close": "business_day.manage",
                 "stock_adjust": "inventory.adjust_stock", "stock_transfer": "inventory.transfer_stock",
                 "sale_refund": "sales.refund"}
        if op.type not in rules:
            failure("ONLINE_ONLY", "This operation requires an online workflow.", 400)
        if op.type == "business_day_open" and payload.get("auto"):
            # Same as online: a sale or expense that finds no open day opens
            # one with the permission that sale/expense itself needs.
            rules["business_day_open"] = {"sale": "sales.create", "expense": "expenses.record", "refund": "sales.refund"}.get(payload.get("trigger"), rules["business_day_open"])
        g["require_permission"](user, rules[op.type])
        if op.type in ("sale_checkout", "expense_create", "sale_refund"):
            day = db.query(g["BusinessDay"]).filter_by(id=payload.get("business_day_id"),
                business_id=user.business_id, location_id=payload.get("location_id")).with_for_update().first()
            if not day or not day.is_open:
                failure("BUSINESS_DAY_CLOSED", "The original Business Day is closed or unavailable.")
        if op.type in ("sale_checkout", "expense_create"):
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
            elif op.type == "business_day_open":
                result = open_business_day(db, user, payload, captured, ref)
            elif op.type == "business_day_close":
                result = close_business_day(db, user, payload, captured, ref)
            elif op.type == "stock_adjust":
                result = adjust_stock(db, user, payload, captured, ref)
            elif op.type == "stock_transfer":
                result = transfer_stock(db, user, payload, captured, ref)
            elif op.type == "sale_refund":
                result = refund_sale(db, user, payload, captured, ref, request)
            else:
                result = g["create_supplier"](g["SupplierCreate"](**payload), user, deferred)
            response = {"op_id": ref, "status": "synced", "result": jsonable_encoder(result),
                        "captured_at": captured.isoformat(), "server_received_at": _utcnow().isoformat(),
                        "user_id": user.id, "business_id": user.business_id, "device_id": str(op.device_id)}
            g["complete_idempotent_mutation"](claim, response)
            db.commit()
            return response
        except ValidationError as exc:
            db.rollback()
            message, fields = validation_reason(exc)
            failure("VALIDATION_ERROR", message, 422, {"fields": fields})
        except HTTPException as exc:
            db.rollback()
            if isinstance(exc.detail, dict):
                raise
            code = {400: "VALIDATION_ERROR", 403: "PERMISSION_CHANGED", 404: "RESOURCE_DELETED", 409: "STOCK_CHANGED"}.get(exc.status_code, "VALIDATION_ERROR")
            failure(code, str(exc.detail), exc.status_code)

    return OfflineDevice
