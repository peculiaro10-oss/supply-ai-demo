"""Audit log location attribution (Activity History filtering).

Adds `audit_logs.location_id` (nullable FK -> locations.id, ON DELETE
SET NULL, indexed) — a real column rather than parsing metadata_json
(stored as Text, not efficiently queryable in PostgreSQL without a proper
JSON column type this table doesn't have), so Activity History can filter
by Location without a full-table JSON scan.

Backfill is deliberately narrow and safe: only rows that already have a
business_day_id get their Location copied FROM that Business Day's own
location_id (a real, already-established relationship — see migration
0025). Every other historical row is left NULL — genuinely unscoped/
business-wide/unknown, never inferred from actor identity, description
text, or any other guess.

Going forward, add_audit() accepts an optional location_id and callers
that resolved a real Location for their event (refunds, POs, Business Day
lifecycle, warehouse transfers, location management itself) pass it.

Revision ID: 0033_audit_location
Revises: 0032_po_requirement_warehouse
"""
from alembic import op
import sqlalchemy as sa

revision = "0033_audit_location"
down_revision = "0032_po_requirement_warehouse"
branch_labels = None
depends_on = None


def _has_column(bind, table: str, column: str) -> bool:
    return column in {c["name"] for c in sa.inspect(bind).get_columns(table)}


def _has_index(bind, table: str, index: str) -> bool:
    return index in {ix["name"] for ix in sa.inspect(bind).get_indexes(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind, "audit_logs", "location_id"):
        op.add_column("audit_logs", sa.Column("location_id", sa.Integer(), sa.ForeignKey("locations.id", ondelete="SET NULL"), nullable=True))
    if not _has_index(bind, "audit_logs", "ix_audit_logs_location"):
        op.create_index("ix_audit_logs_location", "audit_logs", ["location_id"])

    bind.execute(sa.text("""
        UPDATE audit_logs al
        SET location_id = bd.location_id
        FROM business_days bd
        WHERE bd.id = al.business_day_id AND al.location_id IS NULL AND bd.location_id IS NOT NULL
    """))


def downgrade() -> None:
    bind = op.get_bind()
    if _has_index(bind, "audit_logs", "ix_audit_logs_location"):
        op.drop_index("ix_audit_logs_location", table_name="audit_logs")
    if _has_column(bind, "audit_logs", "location_id"):
        op.drop_column("audit_logs", "location_id")
