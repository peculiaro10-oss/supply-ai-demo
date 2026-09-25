"""Stop requiring the retired general_catalog.category value (GC-F1).

`category` is not shared product identity and current code no longer writes
it (see GeneralCatalog's docstring). The column was NOT NULL, which forced
every new row to carry "General". This only relaxes that constraint:
nothing is dropped and historical values are left exactly as they are.

Idempotent (MIGR-001 style): skipped when the column is already nullable or
absent.

Revision ID: 0041_general_catalog_category_retired
Revises: 0040_price_monitor_source_lifecycle
"""
from alembic import op
import sqlalchemy as sa

revision = "0041_general_catalog_category_retired"
down_revision = "0040_price_monitor_source_lifecycle"
branch_labels = None
depends_on = None


def _category_column(bind):
    for column in sa.inspect(bind).get_columns("general_catalog"):
        if column["name"] == "category":
            return column
    return None


def upgrade():
    column = _category_column(op.get_bind())
    if column is not None and not column.get("nullable", True):
        op.alter_column("general_catalog", "category", existing_type=sa.String(), nullable=True)


def downgrade():
    column = _category_column(op.get_bind())
    if column is not None and column.get("nullable", True):
        # Rows written after the upgrade carry no category; give them the old
        # placeholder so the NOT NULL constraint can return. No value is lost.
        op.execute("UPDATE general_catalog SET category = 'General' WHERE category IS NULL")
        op.alter_column("general_catalog", "category", existing_type=sa.String(), nullable=False)
