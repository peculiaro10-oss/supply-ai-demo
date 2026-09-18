"""Add optional Location region/state geography.

Idempotent (MIGR-001): the baseline create_all() already builds this column on
a fresh database, so the add is skipped when it exists.

Revision ID: 0039_location_region
Revises: 0038_offline_devices
"""
from alembic import op
import sqlalchemy as sa

revision = "0039_location_region"
down_revision = "0038_offline_devices"
branch_labels = None
depends_on = None


def _has_column(bind, table: str, column: str) -> bool:
    return column in {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade():
    bind = op.get_bind()
    if not _has_column(bind, "locations", "region"):
        op.add_column("locations", sa.Column("region", sa.String(), nullable=True))


def downgrade():
    raise RuntimeError("Refusing to discard Location region/state data automatically.")
