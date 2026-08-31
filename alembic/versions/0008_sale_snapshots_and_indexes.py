"""Remaining sale-time snapshots + checkout-key index.

Adds:
- sales.unit_price: the actual transaction selling price per unit for this
  line. Previously only total_price was stored and unit price was derived as
  total_price / quantity; storing it explicitly makes the sale's own stated
  price authoritative and removes a division from every read path.
- sales.product_name_snapshot: what the product was called when it was sold,
  so history stays readable after the product is renamed or deleted
  (sales.product_id is ON DELETE SET NULL). Display only — never used in any
  arithmetic, so it can never affect a reported figure.
- ix_sales_business_client_ref: client_ref is now the checkout/transaction
  grouping key that every transaction count does COUNT(DISTINCT ...) over,
  and that checkout's own idempotency lookup filters on. Both want it
  indexed per business.

MIGRATION POLICY FOR EXISTING ROWS — deliberately NO backfill:
Both columns stay NULL on every pre-existing sale row. They are NOT
populated from the products table, because a product's CURRENT name/price is
not evidence of what it was named or sold for at the time of a past sale.
Writing today's values into yesterday's rows would fabricate history and is
exactly what these snapshots exist to prevent. Reads handle NULL explicitly
instead:
- unit_price NULL  -> derived as total_price / quantity, which IS exact
  (total_price was computed as quantity * price at sale time), so legacy
  rows lose nothing at all here.
- product_name_snapshot NULL -> falls back to the live product name, then to
  "Deleted product". Legacy rows for since-deleted products therefore show
  the placeholder rather than a name that was never recorded. This is a
  display-only limitation and affects no financial figure.

This is purely additive: no table dropped or recreated, no existing row
modified, no data deleted.

Revision ID: 0008_sale_snapshots_and_indexes
Revises: 0007_refunds
"""
from alembic import op
import sqlalchemy as sa

revision = "0008_sale_snapshots_and_indexes"
down_revision = "0007_refunds"
branch_labels = None
depends_on = None


def _add_column_if_missing(bind, table_name: str, column: sa.Column):
    inspector = sa.inspect(bind)
    existing = {c["name"] for c in inspector.get_columns(table_name)}
    if column.name not in existing:
        op.add_column(table_name, column)


def upgrade() -> None:
    bind = op.get_bind()
    _add_column_if_missing(bind, "sales", sa.Column("unit_price", sa.Float(), nullable=True))
    _add_column_if_missing(bind, "sales", sa.Column("product_name_snapshot", sa.String(), nullable=True))
    op.execute("CREATE INDEX IF NOT EXISTS ix_sales_business_client_ref ON sales (business_id, client_ref)")


def downgrade() -> None:
    # These are additive, read-fallback-covered snapshot columns, not audit
    # records in their own right — safe to drop without losing a figure that
    # cannot be recomputed (unit_price is exactly total_price/quantity).
    bind = op.get_bind()
    op.execute("DROP INDEX IF EXISTS ix_sales_business_client_ref")
    inspector = sa.inspect(bind)
    existing = {c["name"] for c in inspector.get_columns("sales")}
    if "product_name_snapshot" in existing:
        op.drop_column("sales", "product_name_snapshot")
    if "unit_price" in existing:
        op.drop_column("sales", "unit_price")
