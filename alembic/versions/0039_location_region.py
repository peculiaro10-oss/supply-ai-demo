"""Add optional Location region/state geography.

Revision ID: 0039_location_region
Revises: 0038_offline_devices
"""
from alembic import op
import sqlalchemy as sa

revision = "0039_location_region"
down_revision = "0038_offline_devices"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("locations", sa.Column("region", sa.String(), nullable=True))


def downgrade():
    raise RuntimeError("Refusing to discard Location region/state data automatically.")
