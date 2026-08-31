"""Legacy data backfill — port of main.py's AUTO_CREATE_SCHEMA startup block.

This migration exists so main.py's startup path can stop performing schema
mutation and one-time data repair. It captures ONLY the data-migration half
of that block (the schema/DDL half — every add_column_if_missing() call and
every CREATE INDEX — was already fully covered by 0001-0011: verified
empirically by running the complete chain against an empty database and
diffing every resulting column/index against the current SQLAlchemy models
with zero mismatches. Nothing here changes a table, column, or index.

Every operation below is idempotent (safe to run again with no effect) and
non-destructive (updates/inserts only, no row is ever deleted), exactly
matching the semantics of the startup code it replaces:

1. business_days.status backfill from the historical is_open boolean.
2. sales.business_day_id / expenses.business_day_id backfill by matching
   each row's timestamp into the BusinessDay session window that contains
   it — the same attribution logic sales_history/sales_analytics already
   use for rows that still lack this column.
3. business_subscriptions.card_verified grandfathering for businesses that
   already had a working trial/subscription before card-required trials
   existed — never retroactively locks out an existing business.
4. business_profile.subscription_started_at / trial_started_at and
   purchase_orders.created_at COALESCE backfill for rows recorded before
   these columns existed.
5. A BusinessSubscription row for every BusinessProfile that predates the
   subscription table.
6. Legacy "owner" role renamed to "admin".
7. The warehouse registry seeded from existing Product/WarehouseStock
   free-text warehouse names, for businesses that predate the Warehouse
   table.

Revision ID: 0012_legacy_data_backfill
Revises: 0011_mutation_idempotency
"""
from datetime import datetime, timedelta

import sqlalchemy as sa
from alembic import op

revision = "0012_legacy_data_backfill"
down_revision = "0011_mutation_idempotency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    # --- 1. Business Day status backfill (read of existing is_open truth) ---
    bind.execute(sa.text("UPDATE business_days SET status = 'OPEN' WHERE is_open = TRUE AND (status IS NULL OR status = 'CLOSED')"))
    bind.execute(sa.text("UPDATE business_days SET status = 'CLOSED' WHERE is_open = FALSE AND status IS NULL"))

    # --- 2. sales/expenses.business_day_id backfill --------------------------
    # Matches each row without a business_day_id into the BusinessDay session
    # whose [opened_at, closed_at-or-now) window contains its timestamp.
    # Only fills rows that don't already have a value — safe to re-run, never
    # overwrites, never fabricates a match for a row outside every window.
    from main import BusinessDay, SaleModel, Expense
    from sqlalchemy.orm import Session

    session = Session(bind=bind)
    try:
        days = session.query(BusinessDay).order_by(BusinessDay.opened_at.asc()).all()
        if days:
            for sale in session.query(SaleModel).filter(SaleModel.business_day_id.is_(None)).all():
                for d in days:
                    if d.business_id != sale.business_id:
                        continue
                    upper = d.closed_at or datetime.utcnow()
                    if d.opened_at <= sale.timestamp <= upper:
                        sale.business_day_id = d.id
                        break
            for expense in session.query(Expense).filter(Expense.business_day_id.is_(None)).all():
                for d in days:
                    if d.business_id != expense.business_id:
                        continue
                    upper = d.closed_at or datetime.utcnow()
                    if d.opened_at <= expense.created_at <= upper:
                        expense.business_day_id = d.id
                        break
        session.commit()
    finally:
        session.close()

    # --- 3. card_verified grandfathering --------------------------------------
    # Businesses that already had a working trial/subscription before the
    # card-required trial flow existed are grandfathered so they are never
    # locked out retroactively. Only newly registered businesses (created
    # after that feature shipped) go through the card-required trial flow.
    bind.execute(sa.text(
        "UPDATE business_subscriptions SET card_verified = TRUE "
        "WHERE card_verified = FALSE AND status IN ('trialing','active','past_due','expired','cancelled')"
    ))

    # --- 4. subscription/trial-anchor + purchase_orders.created_at backfill --
    bind.execute(sa.text("UPDATE business_profile SET subscription_started_at = COALESCE(subscription_started_at, CURRENT_TIMESTAMP)"))
    bind.execute(sa.text("UPDATE business_profile SET trial_started_at = COALESCE(trial_started_at, subscription_started_at, CURRENT_TIMESTAMP)"))
    bind.execute(sa.text("UPDATE purchase_orders SET created_at = COALESCE(created_at, CURRENT_TIMESTAMP)"))

    # --- 5. Backfill a BusinessSubscription row for every BusinessProfile ----
    from main import BusinessProfile, get_or_create_subscription
    session = Session(bind=bind)
    try:
        for business in session.query(BusinessProfile).all():
            get_or_create_subscription(session, business, commit=False)
        session.commit()
    finally:
        session.close()

    # --- 6. Legacy role rename ------------------------------------------------
    bind.execute(sa.text("UPDATE users SET role = 'admin' WHERE role = 'owner'"))

    # --- 7. Warehouse registry seed -------------------------------------------
    from main import Product, WarehouseStock, Warehouse
    session = Session(bind=bind)
    try:
        business_ids = {bid for (bid,) in session.query(Product.business_id).distinct().all() if bid is not None}
        business_ids.update({bid for (bid,) in session.query(WarehouseStock.business_id).distinct().all() if bid is not None})
        for business_id in business_ids:
            names = set()
            for (name,) in session.query(Product.warehouse).filter(Product.business_id == business_id).distinct().all():
                if name and str(name).strip():
                    names.add(str(name).strip())
            for (name,) in session.query(WarehouseStock.warehouse).filter(WarehouseStock.business_id == business_id).distinct().all():
                if name and str(name).strip():
                    names.add(str(name).strip())
            if not names:
                names.add("Main Central Warehouse")
            existing = {w.name.casefold(): w for w in session.query(Warehouse).filter(Warehouse.business_id == business_id).all()}
            for name in sorted(names):
                if name.casefold() not in existing:
                    session.add(Warehouse(business_id=business_id, name=name, is_active=True))
        session.commit()
    finally:
        session.close()


def downgrade() -> None:
    # Every operation above is a data repair/backfill, not a reversible
    # schema change — there is no meaningful "undo" (e.g. un-renaming
    # 'admin' back to 'owner' would affect accounts that were always
    # legitimately created as 'admin', not just the ones this migration
    # renamed). Matches this app's existing policy of refusing an automatic
    # downgrade that could not be done safely.
    raise RuntimeError("Refusing to automatically reverse legacy data backfills.")
