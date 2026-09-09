"""Preserve price-monitor history while allowing capacity reclamation.

Revision ID: 0040_price_monitor_source_lifecycle
Revises: 0039_location_region
"""
from alembic import op
import sqlalchemy as sa

revision = "0040_price_monitor_source_lifecycle"
down_revision = "0039_location_region"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "price_monitor_sources",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_index(
        "ix_price_monitor_sources_business_active",
        "price_monitor_sources",
        ["business_id", "is_active"],
    )


def downgrade():
    raise RuntimeError("Refusing to discard the price-monitor source lifecycle automatically.")
