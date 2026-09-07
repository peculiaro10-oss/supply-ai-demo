"""Refund location attribution.

Adds `refund_transactions.location_id` (nullable FK -> locations.id,
ON DELETE SET NULL, indexed). NULL means a refund of a genuinely legacy
sale that predates BusinessDay.location_id — never guessed/backfilled.

Going forward, create_refund() always resolves the ORIGINAL sale's own
Location (via its BusinessDay.location_id) and stores it here — a refund
never lands under whichever Business Day happens to be open elsewhere.

Revision ID: 0028_refund_location
Revises: 0027_location_index_hardening
"""
from alembic import op
import sqlalchemy as sa

revision = "0028_refund_location"
down_revision = "0027_location_index_hardening"
branch_labels = None
depends_on = None


def _has_column(bind, table: str, column: str) -> bool:
    return column in {c["name"] for c in sa.inspect(bind).get_columns(table)}


def _has_index(bind, table: str, index: str) -> bool:
    return index in {ix["name"] for ix in sa.inspect(bind).get_indexes(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind, "refund_transactions", "location_id"):
        op.add_column("refund_transactions", sa.Column("location_id", sa.Integer(), sa.ForeignKey("locations.id", ondelete="SET NULL"), nullable=True))
    if not _has_index(bind, "refund_transactions", "ix_refund_transactions_location"):
        op.create_index("ix_refund_transactions_location", "refund_transactions", ["location_id"])


def downgrade() -> None:
    bind = op.get_bind()
    if _has_index(bind, "refund_transactions", "ix_refund_transactions_location"):
        op.drop_index("ix_refund_transactions_location", table_name="refund_transactions")
    if _has_column(bind, "refund_transactions", "location_id"):
        op.drop_column("refund_transactions", "location_id")
