"""Business Day lifecycle + historical-record integrity.

Adds:
- business_days: status, opener/closer identity, reopen_count, plus a
  UNIQUE(business_id, date) constraint (one row per business per
  business-local date — the same-day duplicate-open guard).
- sales.business_day_id, expenses.business_day_id: direct relationship,
  nullable (existing rows keep matching by timestamp window, exactly as
  before, until/unless backfilled).
- audit_logs.business_day_id, audit_logs.metadata_json: additive, both
  nullable — every existing row and every existing add_audit() call site is
  unaffected. The Business Day activity timeline is a filtered read of this
  same table, not a new log.
- business_day_reopen_requests, sale_adjustments, expense_adjustments: new
  tables (brand new, so create_all's checkfirst handles them safely — the
  explicit op.add_column calls above are only needed for columns being added
  to tables that already exist in a deployed database).

This intentionally does NOT backfill sales.business_day_id /
expenses.business_day_id via SQL here (op.execute would need to duplicate
main.py's business-local-timezone day-window logic) — main.py's own startup
migration path performs that backfill safely (idempotent, read-then-write,
never fabricates a match) for both SQLite and Postgres. Running this
migration is safe with or without that backfill having run yet: every query
that reads business_day_id already falls back to the original timestamp-
window match for rows where it is NULL.

Revision ID: 0002_business_day_integrity
Revises: 0001_baseline_schema
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_business_day_integrity"
down_revision = "0001_baseline_schema"
branch_labels = None
depends_on = None


def _add_column_if_missing(bind, table_name: str, column: sa.Column):
    inspector = sa.inspect(bind)
    existing = {c["name"] for c in inspector.get_columns(table_name)}
    if column.name not in existing:
        op.add_column(table_name, column)


def upgrade() -> None:
    bind = op.get_bind()

    _add_column_if_missing(bind, "business_days", sa.Column("status", sa.String(), nullable=False, server_default="CLOSED"))
    _add_column_if_missing(bind, "business_days", sa.Column("opened_by_id", sa.Integer(), nullable=True))
    _add_column_if_missing(bind, "business_days", sa.Column("opened_by_name", sa.String(), nullable=True))
    _add_column_if_missing(bind, "business_days", sa.Column("opened_by_role", sa.String(), nullable=True))
    _add_column_if_missing(bind, "business_days", sa.Column("closed_by_id", sa.Integer(), nullable=True))
    _add_column_if_missing(bind, "business_days", sa.Column("closed_by_name", sa.String(), nullable=True))
    _add_column_if_missing(bind, "business_days", sa.Column("closed_by_role", sa.String(), nullable=True))
    _add_column_if_missing(bind, "business_days", sa.Column("reopen_count", sa.Integer(), nullable=False, server_default="0"))

    # Derive status from the existing is_open truth — a read of existing
    # data, never an invention of new data.
    op.execute("UPDATE business_days SET status = 'OPEN' WHERE is_open = true AND status = 'CLOSED'")

    inspector = sa.inspect(bind)
    existing_indexes = {ix["name"] for ix in inspector.get_indexes("business_days")}
    if "ux_business_days_business_date" not in existing_indexes:
        op.create_index("ux_business_days_business_date", "business_days", ["business_id", "date"], unique=True)

    _add_column_if_missing(bind, "sales", sa.Column("business_day_id", sa.Integer(), sa.ForeignKey("business_days.id", ondelete="SET NULL"), nullable=True))
    _add_column_if_missing(bind, "expenses", sa.Column("business_day_id", sa.Integer(), sa.ForeignKey("business_days.id", ondelete="SET NULL"), nullable=True))
    _add_column_if_missing(bind, "audit_logs", sa.Column("business_day_id", sa.Integer(), sa.ForeignKey("business_days.id", ondelete="SET NULL"), nullable=True))
    _add_column_if_missing(bind, "audit_logs", sa.Column("metadata_json", sa.Text(), nullable=True))

    # Brand-new tables — checkfirst is exactly right here (unlike for columns
    # on pre-existing tables, it correctly detects a genuinely missing table).
    from main import Base
    Base.metadata.create_all(
        bind=bind, checkfirst=True,
        tables=[
            Base.metadata.tables["business_day_reopen_requests"],
            Base.metadata.tables["sale_adjustments"],
            Base.metadata.tables["expense_adjustments"],
        ],
    )


def downgrade() -> None:
    # Historical-integrity data (audit metadata, reopen requests, corrections)
    # is exactly the kind of record this feature exists to never destroy.
    # Refuse to drop it automatically, matching the baseline's own policy.
    raise RuntimeError("Refusing to drop Business Day integrity data automatically.")
