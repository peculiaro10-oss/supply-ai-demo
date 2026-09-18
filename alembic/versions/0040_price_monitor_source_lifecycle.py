"""Preserve price-monitor history while allowing capacity reclamation.

Idempotent (MIGR-001): the baseline create_all() already builds the column and
index on a fresh database, so each is skipped when it exists.

Revision ID: 0040_price_monitor_source_lifecycle
Revises: 0039_location_region
"""
from alembic import op
import sqlalchemy as sa

revision = "0040_price_monitor_source_lifecycle"
down_revision = "0039_location_region"
branch_labels = None
depends_on = None


def _has_column(bind, table: str, column: str) -> bool:
    return column in {c["name"] for c in sa.inspect(bind).get_columns(table)}


def _has_index(bind, table: str, index: str) -> bool:
    return index in {i["name"] for i in sa.inspect(bind).get_indexes(table)}


def upgrade():
    bind = op.get_bind()
    if not _has_column(bind, "price_monitor_sources", "is_active"):
        op.add_column(
            "price_monitor_sources",
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        )
    if not _has_index(bind, "price_monitor_sources", "ix_price_monitor_sources_business_active"):
        op.create_index(
            "ix_price_monitor_sources_business_active",
            "price_monitor_sources",
            ["business_id", "is_active"],
        )


def downgrade():
    raise RuntimeError("Refusing to discard the price-monitor source lifecycle automatically.")
