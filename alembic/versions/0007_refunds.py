"""Refund system: refund_transactions, refund_lines tables.

Adds explicit refund records tied to original sales — the original
SaleModel row is never modified or deleted; a refund is a separate,
additive financial event that references it (RefundLine.original_sale_id).
See main.py's RefundTransaction/RefundLine model docstrings for the full
design (business-day ownership, price/cost snapshotting, restock behavior).

This is purely additive: two brand-new tables (created via
Base.metadata.create_all with checkfirst=True, the same pattern
0002_business_day_integrity used for its own new tables) plus supporting
indexes. No existing table, column, or row is touched. No business/user/
product/sale/expense/audit data is modified.

A partial unique index on (business_id, client_ref) WHERE client_ref IS NOT
NULL protects against a double-submitted refund creating two
RefundTransaction rows for the same idempotency key — refunds with no
client_ref (the common case today, since the frontend generates one only
for double-submit protection) are completely unconstrained by it.

Revision ID: 0007_refunds
Revises: 0006_performance_indexes
"""
from alembic import op
import sqlalchemy as sa

revision = "0007_refunds"
down_revision = "0006_performance_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    from main import Base
    Base.metadata.create_all(
        bind=bind, checkfirst=True,
        tables=[
            Base.metadata.tables["refund_transactions"],
            Base.metadata.tables["refund_lines"],
        ],
    )

    op.execute("CREATE INDEX IF NOT EXISTS ix_refund_lines_business_day ON refund_lines (business_id, business_day_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_refund_lines_original_sale ON refund_lines (original_sale_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_refund_transactions_business_day ON refund_transactions (business_id, business_day_id)")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_refund_transactions_business_client_ref ON refund_transactions (business_id, client_ref) WHERE client_ref IS NOT NULL")


def downgrade() -> None:
    # Refund records are exactly the kind of financial history this feature
    # exists to never lose — refuse to drop them automatically, matching
    # the baseline/integrity migrations' own policy for other financial/
    # audit data in this app.
    raise RuntimeError("Refusing to drop refund tables/data automatically.")
