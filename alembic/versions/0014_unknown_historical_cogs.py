"""Preserve unknown legacy COGS through refunds.

Legacy sales may legitimately have sales.unit_cost_at_sale = NULL because no
cost snapshot was recorded at sale time. Refund cost fields must be nullable
too, otherwise refunding one of those rows forces the application to invent a
current/zero cost.

No historical values are backfilled or rewritten.

Revision ID: 0014_unknown_historical_cogs
Revises: 0013_barcode_catalog
"""
from alembic import op
import sqlalchemy as sa

revision = "0014_unknown_historical_cogs"
down_revision = "0013_barcode_catalog"
branch_labels = None
depends_on = None


def _make_nullable_if_needed(table_name: str, column_name: str) -> None:
    bind = op.get_bind()
    column = next(c for c in sa.inspect(bind).get_columns(table_name) if c["name"] == column_name)
    if not column.get("nullable", True):
        op.alter_column(table_name, column_name, existing_type=sa.Float(), nullable=True)


def upgrade() -> None:
    _make_nullable_if_needed("refund_transactions", "refund_cost_total")
    _make_nullable_if_needed("refund_lines", "unit_cost")
    _make_nullable_if_needed("refund_lines", "refund_cost")


def downgrade() -> None:
    raise RuntimeError("Refusing to make unknown historical refund costs non-nullable; that would require fabricating values.")
