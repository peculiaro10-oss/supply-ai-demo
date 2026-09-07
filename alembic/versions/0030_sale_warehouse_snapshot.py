"""Sale-time warehouse snapshot.

Adds `sales.warehouse_name_snapshot` (nullable String). NULL for every
sale recorded before this column existed — never backfilled/guessed, since
Product.warehouse (the only thing that could be used to backfill it) is a
MUTABLE "current" pointer that may have changed since the sale (a transfer
or reassignment does not rewrite history) — backfilling from it would
fabricate a snapshot that might not match what was actually true at sale
time.

Going forward, sales_checkout() stamps the warehouse each line's stock was
actually decremented from, at the moment of sale. create_refund()'s restock
logic (and any other durable historical Location lookup for a Sale) prefers
this snapshot over Product's current warehouse, falling back to the
current warehouse only for legacy pre-migration rows exactly as before.

Revision ID: 0030_sale_warehouse_snapshot
Revises: 0029_purchase_order_location
"""
from alembic import op
import sqlalchemy as sa

revision = "0030_sale_warehouse_snapshot"
down_revision = "0029_purchase_order_location"
branch_labels = None
depends_on = None


def _has_column(bind, table: str, column: str) -> bool:
    return column in {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind, "sales", "warehouse_name_snapshot"):
        op.add_column("sales", sa.Column("warehouse_name_snapshot", sa.String(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, "sales", "warehouse_name_snapshot"):
        op.drop_column("sales", "warehouse_name_snapshot")
