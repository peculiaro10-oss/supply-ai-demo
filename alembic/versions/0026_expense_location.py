"""Expense location attribution.

Adds `expenses.location_id` (nullable FK -> locations.id, ON DELETE
SET NULL, indexed). NULL means genuinely unknown / pre-location-
architecture history — this migration does NOT backfill it from creator
identity, the business's Main Location, a free-text note, or a created_at
timestamp; none of those are an authoritative source of "which branch was
this expense actually recorded for", so inventing one would fabricate
history (see the Expense model docstring in main.py).

Going forward, create_expense() always resolves and stores a real
location_id for every NEW expense (or refuses the request if the business
has more than one active Location and none was chosen — see
resolve_expense_location()).

Revision ID: 0026_expense_location
Revises: 0025_business_day_per_location
"""
from alembic import op
import sqlalchemy as sa

revision = "0026_expense_location"
down_revision = "0025_business_day_per_location"
branch_labels = None
depends_on = None


def _has_column(bind, table: str, column: str) -> bool:
    return column in {c["name"] for c in sa.inspect(bind).get_columns(table)}


def _has_index(bind, table: str, index: str) -> bool:
    return index in {ix["name"] for ix in sa.inspect(bind).get_indexes(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind, "expenses", "location_id"):
        op.add_column("expenses", sa.Column("location_id", sa.Integer(), sa.ForeignKey("locations.id", ondelete="SET NULL"), nullable=True))
    if not _has_index(bind, "expenses", "ix_expenses_location"):
        op.create_index("ix_expenses_location", "expenses", ["location_id"])


def downgrade() -> None:
    bind = op.get_bind()
    if _has_index(bind, "expenses", "ix_expenses_location"):
        op.drop_index("ix_expenses_location", table_name="expenses")
    if _has_column(bind, "expenses", "location_id"):
        op.drop_column("expenses", "location_id")
