"""Barcode lookup: General Catalog identity fields + per-business barcode uniqueness.

Adds two purely additive columns to general_catalog:
- size: optional product-identity size/variant hint (e.g. from UPCitemdb's
  dimension/weight fields, or a business's own product.size). Never
  pricing, never quantity.
- source: "business_submission" (default — the existing auto-upsert-on-
  product-create/edit path) or "upcitemdb" (a cached provider lookup). See
  main.py's GeneralCatalog docstring and upsert_general_catalog_identity().

`category` is intentionally left untouched here — see the GeneralCatalog
class docstring in main.py for why the column stays physically present
(deprecated, not dropped) while the application stops writing/reading it as
shared identity.

Also adds a partial unique index on products(business_id, barcode) WHERE
barcode IS NOT NULL — defense-in-depth against a duplicate barcode being
inserted twice for the same business under a concurrent request (the
application already checks for this in main.py's create_product/
update_product, but a unique index is the only guarantee that survives two
literally-simultaneous requests). Confirmed safe to add outright: a live
audit of production data before writing this migration found zero existing
non-empty Product.barcode values, so there is nothing that could violate it.

Revision ID: 0013_barcode_catalog
Revises: 0012_legacy_data_backfill
"""
from alembic import op
import sqlalchemy as sa

revision = "0013_barcode_catalog"
down_revision = "0012_legacy_data_backfill"
branch_labels = None
depends_on = None


def _add_column_if_missing(bind, table_name: str, column: sa.Column):
    inspector = sa.inspect(bind)
    existing = {c["name"] for c in inspector.get_columns(table_name)}
    if column.name not in existing:
        op.add_column(table_name, column)


def upgrade() -> None:
    bind = op.get_bind()
    _add_column_if_missing(bind, "general_catalog", sa.Column("size", sa.String(), nullable=True))
    _add_column_if_missing(
        bind, "general_catalog",
        sa.Column("source", sa.String(), nullable=False, server_default="business_submission"),
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_products_business_barcode "
        "ON products (business_id, barcode) WHERE barcode IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ux_products_business_barcode")
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {c["name"] for c in inspector.get_columns("general_catalog")}
    if "source" in existing:
        op.drop_column("general_catalog", "source")
    if "size" in existing:
        op.drop_column("general_catalog", "size")
