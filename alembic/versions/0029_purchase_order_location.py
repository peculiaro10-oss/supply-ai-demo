"""Purchase Order location attribution.

Adds `purchase_orders.location_id` (nullable FK -> locations.id,
ON DELETE SET NULL, indexed). NULL means a legacy PO generated before this
column existed, or (rare) a product whose warehouse had no Location
assigned at generation time — never guessed/backfilled.

Going forward, generate_po() groups uncovered restock requirements BY
Location (derived from each product's own Warehouse.location_id) and
creates one separate DRAFT PurchaseOrder per Location — never one PO
spanning two branches.

Revision ID: 0029_purchase_order_location
Revises: 0028_refund_location
"""
from alembic import op
import sqlalchemy as sa

revision = "0029_purchase_order_location"
down_revision = "0028_refund_location"
branch_labels = None
depends_on = None


def _has_column(bind, table: str, column: str) -> bool:
    return column in {c["name"] for c in sa.inspect(bind).get_columns(table)}


def _has_index(bind, table: str, index: str) -> bool:
    return index in {ix["name"] for ix in sa.inspect(bind).get_indexes(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind, "purchase_orders", "location_id"):
        op.add_column("purchase_orders", sa.Column("location_id", sa.Integer(), sa.ForeignKey("locations.id", ondelete="SET NULL"), nullable=True))
    if not _has_index(bind, "purchase_orders", "ix_purchase_orders_location"):
        op.create_index("ix_purchase_orders_location", "purchase_orders", ["location_id"])


def downgrade() -> None:
    bind = op.get_bind()
    if _has_index(bind, "purchase_orders", "ix_purchase_orders_location"):
        op.drop_index("ix_purchase_orders_location", table_name="purchase_orders")
    if _has_column(bind, "purchase_orders", "location_id"):
        op.drop_column("purchase_orders", "location_id")
