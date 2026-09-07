"""Currency-at-creation snapshots for financial records.

Adds a nullable `currency_snapshot` String column (a canonical currency
CODE like "NGN"/"USD"/"GBP" — never a formatted symbol) to:

- sales.currency_snapshot
- expenses.currency_snapshot
- refund_transactions.currency_snapshot
- purchase_orders.currency_snapshot

Going forward, every NEW record of each type stamps this once, at
creation, from its own operating Location's currency (sales_checkout(),
create_expense(), create_refund(), generate_po() — see each function's own
comments) and NEVER re-derives it from Location.currency again later. This
is what makes a future Location currency change safe: old records keep
reading as what they actually were, never silently reinterpreted (see
update_location()'s existing currency-change guard, which stays in place
regardless — snapshots make history safe, they do not by themselves make
a live currency change across in-flight/open sessions safe).

BACKFILL (documented precisely, no invented FX, no guessed currency):
1. Where the record has a resolvable Location (via its own location_id,
   or — for Sale — via BusinessDay.location_id), use that Location's
   CURRENT currency. This is only safe because update_location() already
   blocks a currency change at any Location with existing financial
   activity — so a Location's current currency IS the currency every one
   of its historical records was actually denominated in.
2. Where no Location is resolvable at all (a genuinely legacy record from
   before the Location architecture existed), fall back to that record's
   own business's BusinessProfile.currency — the ONE currency field that
   existed for a business before Locations did, so this is restating
   already-true historical data, never fabricating an exchange rate or a
   guessed value.
3. Where NEITHER is resolvable (an orphaned business_id), left NULL.

Revision ID: 0034_currency_snapshots
Revises: 0033_audit_location
"""
from alembic import op
import sqlalchemy as sa

revision = "0034_currency_snapshots"
down_revision = "0033_audit_location"
branch_labels = None
depends_on = None


def _has_column(bind, table: str, column: str) -> bool:
    return column in {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()

    for table in ("sales", "expenses", "refund_transactions", "purchase_orders"):
        if not _has_column(bind, table, "currency_snapshot"):
            op.add_column(table, sa.Column("currency_snapshot", sa.String(), nullable=True))

    # --- Sales: via BusinessDay.location_id -> Location.currency ------------
    bind.execute(sa.text("""
        UPDATE sales s
        SET currency_snapshot = l.currency
        FROM business_days bd
        JOIN locations l ON l.id = bd.location_id
        WHERE s.business_day_id = bd.id AND s.currency_snapshot IS NULL AND l.currency IS NOT NULL
    """))
    # Legacy fallback: no resolvable Location -> that business's own
    # BusinessProfile.currency (the pre-Location historical convention).
    bind.execute(sa.text("""
        UPDATE sales s
        SET currency_snapshot = bp.currency
        FROM business_profile bp
        WHERE bp.id = s.business_id AND s.currency_snapshot IS NULL AND bp.currency IS NOT NULL
    """))

    # --- Expenses: via Expense.location_id -----------------------------------
    bind.execute(sa.text("""
        UPDATE expenses e
        SET currency_snapshot = l.currency
        FROM locations l
        WHERE l.id = e.location_id AND e.currency_snapshot IS NULL AND l.currency IS NOT NULL
    """))
    bind.execute(sa.text("""
        UPDATE expenses e
        SET currency_snapshot = bp.currency
        FROM business_profile bp
        WHERE bp.id = e.business_id AND e.currency_snapshot IS NULL AND bp.currency IS NOT NULL
    """))

    # --- Refund transactions: via RefundTransaction.location_id --------------
    bind.execute(sa.text("""
        UPDATE refund_transactions rt
        SET currency_snapshot = l.currency
        FROM locations l
        WHERE l.id = rt.location_id AND rt.currency_snapshot IS NULL AND l.currency IS NOT NULL
    """))
    bind.execute(sa.text("""
        UPDATE refund_transactions rt
        SET currency_snapshot = bp.currency
        FROM business_profile bp
        WHERE bp.id = rt.business_id AND rt.currency_snapshot IS NULL AND bp.currency IS NOT NULL
    """))

    # --- Purchase orders: via PurchaseOrder.location_id -----------------------
    bind.execute(sa.text("""
        UPDATE purchase_orders po
        SET currency_snapshot = l.currency
        FROM locations l
        WHERE l.id = po.location_id AND po.currency_snapshot IS NULL AND l.currency IS NOT NULL
    """))
    bind.execute(sa.text("""
        UPDATE purchase_orders po
        SET currency_snapshot = bp.currency
        FROM business_profile bp
        WHERE bp.id = po.business_id AND po.currency_snapshot IS NULL AND bp.currency IS NOT NULL
    """))


def downgrade() -> None:
    bind = op.get_bind()
    for table in ("sales", "expenses", "refund_transactions", "purchase_orders"):
        if _has_column(bind, table, "currency_snapshot"):
            op.drop_column(table, "currency_snapshot")
