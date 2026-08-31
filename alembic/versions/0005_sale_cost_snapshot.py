"""Per-sale historical cost snapshot.

Adds sales.unit_cost_at_sale: the product's cost_price AT THE MOMENT a sale
was recorded, set once at checkout and never touched again. Historical
profit (Gross/Net Profit, COGS) must always read this snapshot instead of
the product's CURRENT cost_price, so changing a product's cost later can
never rewrite old profit figures (see main.py's compute_financial_summary
and the SaleModel.unit_cost_at_sale column comment).

This is purely additive: no table is dropped or recreated, no existing row
is modified. Sales recorded before this migration have unit_cost_at_sale =
NULL — compute_financial_summary() falls back to the product's current
cost_price for those specific rows at query time only, as the best
available answer for data that predates the snapshot. This migration does
NOT backfill that NULL with any guessed value, deliberately: doing so would
mean fabricating a historical cost that was never actually recorded, which
is exactly the kind of rewrite this feature exists to prevent.

Revision ID: 0005_sale_cost_snapshot
Revises: 0004_business_day_multi_session
"""
from alembic import op
import sqlalchemy as sa

revision = "0005_sale_cost_snapshot"
down_revision = "0004_business_day_multi_session"
branch_labels = None
depends_on = None


def _add_column_if_missing(bind, table_name: str, column: sa.Column):
    inspector = sa.inspect(bind)
    existing = {c["name"] for c in inspector.get_columns(table_name)}
    if column.name not in existing:
        op.add_column(table_name, column)


def upgrade() -> None:
    bind = op.get_bind()
    _add_column_if_missing(bind, "sales", sa.Column("unit_cost_at_sale", sa.Float(), nullable=True))


def downgrade() -> None:
    # Purely a derived/cache-style figure captured at sale time, never user-
    # entered data and never an audit record on its own (the audit trail for
    # the sale itself is untouched) — safe to drop without losing anything
    # that couldn't be re-derived (imperfectly, via current cost_price) the
    # same way pre-migration rows already are.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {c["name"] for c in inspector.get_columns("sales")}
    if "unit_cost_at_sale" in existing:
        op.drop_column("sales", "unit_cost_at_sale")
