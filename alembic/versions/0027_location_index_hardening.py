"""Location index hardening — repair migration, not a rewrite of 0024.

0024/0025/0026 only ever CREATE their index inside the branch that also
ADDS the column ("if not _has_column: add column; create index"). On a
database where a column was already added by some earlier/manual step
before its migration ran — this project has a documented history of
production columns being added by hand before a migration existed for
them — a later `alembic upgrade head` would see the column already present,
skip that whole branch, and silently never create the index either.

This migration is deliberately NOT an edit to 0024/0025/0026 themselves
(never safe once a migration may already be applied in production — see
main.py's own migration-safety rules). It independently re-checks each
column/index pair added by the multi-location work and creates whichever
index is missing, regardless of why it's missing. Fully idempotent and
side-effect-free if everything is already correct.

Revision ID: 0027_location_index_hardening
Revises: 0026_expense_location
"""
from alembic import op
import sqlalchemy as sa

revision = "0027_location_index_hardening"
down_revision = "0026_expense_location"
branch_labels = None
depends_on = None


def _has_column(bind, table: str, column: str) -> bool:
    return column in {c["name"] for c in sa.inspect(bind).get_columns(table)}


def _has_index(bind, table: str, index: str) -> bool:
    return index in {ix["name"] for ix in sa.inspect(bind).get_indexes(table)}


def _ensure_index(bind, index_name: str, table: str, columns: list) -> None:
    if _has_column(bind, table, columns[0]) and not _has_index(bind, table, index_name):
        op.create_index(index_name, table, columns)


def upgrade() -> None:
    bind = op.get_bind()

    # locations.business_id — from 0024
    if not _has_index(bind, "locations", "ix_locations_business"):
        op.create_index("ix_locations_business", "locations", ["business_id"])

    # warehouses.location_id — from 0024
    _ensure_index(bind, "ix_warehouses_location", "warehouses", ["location_id"])

    # business_days.location_id — from 0024
    _ensure_index(bind, "ix_business_days_location", "business_days", ["location_id"])

    # expenses.location_id — from 0026
    _ensure_index(bind, "ix_expenses_location", "expenses", ["location_id"])

    # The per-location active-Business-Day partial unique index from 0025 —
    # re-verified here too, since it is the one DB-level invariant the whole
    # multi-location Business Day model depends on, and a partial unique
    # index is exactly the kind of thing that could have been silently lost
    # (e.g. a manual DROP INDEX during troubleshooting) without any column
    # ever changing to reveal it.
    if not _has_index(bind, "business_days", "ux_business_days_one_active_per_location"):
        op.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_business_days_one_active_per_location "
            "ON business_days (business_id, location_id) WHERE is_open = true"
        )


def downgrade() -> None:
    # Purely additive/repair — nothing here to safely reverse (dropping a
    # correctness-critical index would only reintroduce the exact defect
    # this migration exists to fix). No-op by design.
    pass
