"""Warehouse identity hardening.

Warehouse NAMES are display labels; Warehouse IDs are identity. Adds
authoritative `warehouse_id` FKs to the three tables that previously only
related to a warehouse through its mutable name string:

- products.warehouse_id            (the product's PRIMARY/default warehouse)
- warehouse_stocks.warehouse_id    (per-warehouse stock row identity)
- sales.warehouse_id_at_sale       (which warehouse a sale actually used)

All three are nullable FKs (ON DELETE SET NULL), indexed, and backfilled
ONLY where business_id + the existing warehouse name string maps to
EXACTLY ONE real Warehouse row — which is always deterministic here,
because `warehouses` already carries a UNIQUE (business_id, name)
constraint (uq_warehouse_business_name, see the Warehouse model): a given
name can never resolve to more than one Warehouse within one business. A
row whose warehouse name has no matching Warehouse at all (should not
normally happen, but is not assumed) is left NULL rather than guessed.

The existing string columns (products.warehouse, warehouse_stocks.warehouse,
sales.warehouse_name_snapshot) are NOT removed — they remain synchronized
compatibility/display snapshots going forward (see main.py's model
docstrings for the full source-of-truth contract).

Revision ID: 0031_warehouse_identity
Revises: 0030_sale_warehouse_snapshot
"""
from alembic import op
import sqlalchemy as sa

revision = "0031_warehouse_identity"
down_revision = "0030_sale_warehouse_snapshot"
branch_labels = None
depends_on = None


def _has_column(bind, table: str, column: str) -> bool:
    return column in {c["name"] for c in sa.inspect(bind).get_columns(table)}


def _has_index(bind, table: str, index: str) -> bool:
    return index in {ix["name"] for ix in sa.inspect(bind).get_indexes(table)}


def upgrade() -> None:
    bind = op.get_bind()

    if not _has_column(bind, "products", "warehouse_id"):
        op.add_column("products", sa.Column("warehouse_id", sa.Integer(), sa.ForeignKey("warehouses.id", ondelete="SET NULL"), nullable=True))
    if not _has_index(bind, "products", "ix_products_warehouse_id"):
        op.create_index("ix_products_warehouse_id", "products", ["warehouse_id"])
    bind.execute(sa.text("""
        UPDATE products p
        SET warehouse_id = w.id
        FROM warehouses w
        WHERE w.business_id = p.business_id AND w.name = p.warehouse AND p.warehouse_id IS NULL
    """))

    if not _has_column(bind, "warehouse_stocks", "warehouse_id"):
        op.add_column("warehouse_stocks", sa.Column("warehouse_id", sa.Integer(), sa.ForeignKey("warehouses.id", ondelete="SET NULL"), nullable=True))
    if not _has_index(bind, "warehouse_stocks", "ix_warehouse_stocks_warehouse_id"):
        op.create_index("ix_warehouse_stocks_warehouse_id", "warehouse_stocks", ["warehouse_id"])
    bind.execute(sa.text("""
        UPDATE warehouse_stocks ws
        SET warehouse_id = w.id
        FROM warehouses w
        WHERE w.business_id = ws.business_id AND w.name = ws.warehouse AND ws.warehouse_id IS NULL
    """))

    if not _has_column(bind, "sales", "warehouse_id_at_sale"):
        op.add_column("sales", sa.Column("warehouse_id_at_sale", sa.Integer(), sa.ForeignKey("warehouses.id", ondelete="SET NULL"), nullable=True))
    if not _has_index(bind, "sales", "ix_sales_warehouse_id_at_sale"):
        op.create_index("ix_sales_warehouse_id_at_sale", "sales", ["warehouse_id_at_sale"])
    # Backfill ONLY from warehouse_name_snapshot (the sale's own durable
    # record of what warehouse it used) — never from Product's current
    # warehouse, which may have changed since. A sale with no snapshot
    # (pre-migration-0030 legacy row) is left NULL, honestly.
    bind.execute(sa.text("""
        UPDATE sales s
        SET warehouse_id_at_sale = w.id
        FROM warehouses w
        WHERE w.business_id = s.business_id AND w.name = s.warehouse_name_snapshot
          AND s.warehouse_id_at_sale IS NULL AND s.warehouse_name_snapshot IS NOT NULL
    """))


def downgrade() -> None:
    bind = op.get_bind()
    for table, index, column in [
        ("sales", "ix_sales_warehouse_id_at_sale", "warehouse_id_at_sale"),
        ("warehouse_stocks", "ix_warehouse_stocks_warehouse_id", "warehouse_id"),
        ("products", "ix_products_warehouse_id", "warehouse_id"),
    ]:
        if _has_index(bind, table, index):
            op.drop_index(index, table_name=table)
        if _has_column(bind, table, column):
            op.drop_column(table, column)
