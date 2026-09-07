"""Purchase Order Requirement warehouse identity.

The prior uniqueness rule — one unresolved requirement per (business,
product) — assumed a product can only ever be short in one place. Once a
product can hold stock in more than one warehouse (see WarehouseStock's
docstring in main.py), that assumption is false: the SAME product can
genuinely be low in Lagos and healthy in London at the same time, and each
must be its own independent, independently-resolvable requirement.

This migration:
1. Adds `purchase_order_requirements.warehouse_id` (nullable FK ->
   warehouses.id, ON DELETE SET NULL, indexed).
2. Backfills it from each requirement's own Product.warehouse_id (itself
   backfilled by migration 0031) — the product's primary warehouse at the
   time this requirement was created. Left NULL wherever that is not
   resolvable (never guessed).
3. Replaces the old single partial unique index
   `ux_por_active_per_product` on (business_id, product_id) WHERE
   resolved_at IS NULL with TWO partial unique indexes so the invariant is
   correct for BOTH row shapes at once:
   - `ux_por_active_per_product_warehouse` on
     (business_id, product_id, warehouse_id) WHERE resolved_at IS NULL AND
     warehouse_id IS NOT NULL — the new, correct per-warehouse rule.
   - `ux_por_active_per_product_legacy` on (business_id, product_id) WHERE
     resolved_at IS NULL AND warehouse_id IS NULL — preserves the OLD
     product-only rule exactly for any row that could not be backfilled
     with a warehouse_id, so no legacy state becomes newly ambiguous.

Revision ID: 0032_po_requirement_warehouse
Revises: 0031_warehouse_identity
"""
from alembic import op
import sqlalchemy as sa

revision = "0032_po_requirement_warehouse"
down_revision = "0031_warehouse_identity"
branch_labels = None
depends_on = None


def _has_column(bind, table: str, column: str) -> bool:
    return column in {c["name"] for c in sa.inspect(bind).get_columns(table)}


def _has_index(bind, table: str, index: str) -> bool:
    return index in {ix["name"] for ix in sa.inspect(bind).get_indexes(table)}


def upgrade() -> None:
    bind = op.get_bind()

    if not _has_column(bind, "purchase_order_requirements", "warehouse_id"):
        op.add_column("purchase_order_requirements", sa.Column("warehouse_id", sa.Integer(), sa.ForeignKey("warehouses.id", ondelete="SET NULL"), nullable=True))
    if not _has_index(bind, "purchase_order_requirements", "ix_por_warehouse"):
        op.create_index("ix_por_warehouse", "purchase_order_requirements", ["warehouse_id"])

    bind.execute(sa.text("""
        UPDATE purchase_order_requirements por
        SET warehouse_id = p.warehouse_id
        FROM products p
        WHERE p.id = por.product_id AND por.warehouse_id IS NULL AND p.warehouse_id IS NOT NULL
    """))

    if _has_index(bind, "purchase_order_requirements", "ux_por_active_per_product"):
        op.execute("DROP INDEX IF EXISTS ux_por_active_per_product")

    if not _has_index(bind, "purchase_order_requirements", "ux_por_active_per_product_warehouse"):
        op.create_index(
            "ux_por_active_per_product_warehouse", "purchase_order_requirements",
            ["business_id", "product_id", "warehouse_id"], unique=True,
            postgresql_where=sa.text("resolved_at IS NULL AND warehouse_id IS NOT NULL"),
        )
    if not _has_index(bind, "purchase_order_requirements", "ux_por_active_per_product_legacy"):
        op.create_index(
            "ux_por_active_per_product_legacy", "purchase_order_requirements",
            ["business_id", "product_id"], unique=True,
            postgresql_where=sa.text("resolved_at IS NULL AND warehouse_id IS NULL"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    op.execute("DROP INDEX IF EXISTS ux_por_active_per_product_legacy")
    op.execute("DROP INDEX IF EXISTS ux_por_active_per_product_warehouse")
    if not _has_index(bind, "purchase_order_requirements", "ux_por_active_per_product"):
        op.create_index(
            "ux_por_active_per_product", "purchase_order_requirements",
            ["business_id", "product_id"], unique=True,
            postgresql_where=sa.text("resolved_at IS NULL"),
        )
    if _has_index(bind, "purchase_order_requirements", "ix_por_warehouse"):
        op.drop_index("ix_por_warehouse", table_name="purchase_order_requirements")
    if _has_column(bind, "purchase_order_requirements", "warehouse_id"):
        op.drop_column("purchase_order_requirements", "warehouse_id")
