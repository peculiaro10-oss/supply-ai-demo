"""Preserve which pricing tier a sale line actually used.

Adds sales.pricing_type: "retail" | "wholesale" | "negotiated", copied
verbatim from SalesCheckoutItem.price_mode at checkout (see sales_checkout()
in main.py). Purely descriptive/traceability — SaleModel.unit_price is
already the complete, authoritative charged amount on its own (see its
column comment), so nothing anywhere is ever re-derived FROM pricing_type.
This only answers "which catalog tier (or negotiation) produced that
price?" for display/audit, and — like every other sale-time snapshot on
this table (unit_cost_at_sale, unit_price, product_name_snapshot) — is
fixed at the moment of sale and never rewritten by a later change to
Product.retail_price/wholesale_price/cost_price.

Purely additive: no existing column, table, or row is touched. Sales
recorded before this migration have pricing_type = NULL — a genuinely
unknown historical fact (the checkout code before this change never
recorded which tier was used), never guessed or backfilled from the
product's current prices.

Revision ID: 0016_sale_pricing_type
Revises: 0015_paystack_refund_state
"""
from alembic import op
import sqlalchemy as sa

revision = "0016_sale_pricing_type"
down_revision = "0015_paystack_refund_state"
branch_labels = None
depends_on = None


def _add_column_if_missing(bind, table_name: str, column: sa.Column):
    inspector = sa.inspect(bind)
    existing = {c["name"] for c in inspector.get_columns(table_name)}
    if column.name not in existing:
        op.add_column(table_name, column)


def upgrade() -> None:
    bind = op.get_bind()
    _add_column_if_missing(bind, "sales", sa.Column("pricing_type", sa.String(), nullable=True))


def downgrade() -> None:
    # Same policy as 0005_sale_cost_snapshot's own cost snapshot: a derived/
    # descriptive field, safe to drop without losing anything that couldn't
    # already be re-derived (imperfectly) from unit_price vs the product's
    # current retail/wholesale prices, the same way pre-migration rows
    # already have to be read.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {c["name"] for c in inspector.get_columns("sales")}
    if "pricing_type" in existing:
        op.drop_column("sales", "pricing_type")
