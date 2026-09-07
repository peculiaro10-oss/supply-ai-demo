"""Purchase Order restock-requirement coverage tracking.

Adds one new table, purchase_order_requirements, so duplicate-Purchase-Order
prevention can be backend-authoritative and concurrency-safe instead of
parsing the human-readable email_draft text (see generate_po() in main.py).

Purely additive: no existing table/column is touched, no existing row is
modified or backfilled (there is nothing to backfill — a pre-existing SENT
or DRAFT PO's coverage was never tracked before this, and it would be
fabricated data to guess it retroactively; existing POs simply have no
requirement rows and stop blocking nothing until the NEXT time generate_po()
runs for a still-outstanding shortage on the same product, at which point a
fresh requirement row is created going forward).

The partial unique index (business_id, product_id) WHERE resolved_at IS NULL
is what makes "only one active requirement per product" concurrency-safe at
the database level: two simultaneous requests can only ever have one INSERT
for the same (business, product) succeed while resolved_at is still null.

Revision ID: 0023_purchase_order_requirements
Revises: 0022_permissions_activity_presence
"""
from alembic import op
import sqlalchemy as sa

revision = "0023_purchase_order_requirements"
down_revision = "0022_permissions_activity_presence"
branch_labels = None
depends_on = None


def _has_table(bind, table: str) -> bool:
    return table in sa.inspect(bind).get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, "purchase_order_requirements"):
        op.create_table(
            "purchase_order_requirements",
            sa.Column("id", sa.Integer(), primary_key=True, index=True),
            sa.Column("business_id", sa.Integer(), sa.ForeignKey("business_profile.id", ondelete="CASCADE"), nullable=False),
            sa.Column("purchase_order_id", sa.Integer(), sa.ForeignKey("purchase_orders.id", ondelete="CASCADE"), nullable=False),
            sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("resolved_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_por_business", "purchase_order_requirements", ["business_id"])
        op.create_index("ix_por_purchase_order", "purchase_order_requirements", ["purchase_order_id"])
        op.create_index("ix_por_product", "purchase_order_requirements", ["product_id"])
        # Partial unique index — PostgreSQL-specific (postgresql_where).
        # This is the actual concurrency-safety mechanism (see module
        # docstring); on a non-Postgres test DB without partial-index
        # support, the plain indexes above still let the app run, just
        # without the DB-level race guarantee (generate_po()'s own
        # begin_nested()/IntegrityError handling then simply never triggers
        # there, which is a acceptable no-op on SQLite dev/test only).
        op.create_index(
            "ux_por_active_per_product", "purchase_order_requirements",
            ["business_id", "product_id"], unique=True,
            postgresql_where=sa.text("resolved_at IS NULL"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "purchase_order_requirements"):
        op.drop_table("purchase_order_requirements")
