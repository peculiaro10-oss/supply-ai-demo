"""Centralized permissions, Activity History categorization, and Team
Presence activity tracking.

Purely additive. No existing column, table, index, or row is dropped,
renamed, or destroyed. Every statement is idempotent (checked against
`information_schema` via SQLAlchemy's inspector before acting) because the
columns discussed for this work may already have been applied directly in
Supabase ahead of this migration existing in the repo — this migration must
be safe to run whether or not that already happened.

1. users.permission_overrides (TEXT, NULL) — JSON object of explicit
   permission grants/denials layered on top of the user's role defaults.
   Absence of a key means "inherit the role default"; this column is never
   backfilled with a computed snapshot of those defaults (that would turn a
   role-default into a frozen, permanently-serialized override the moment a
   user's role later changed) — it starts NULL (== "no overrides at all")
   for every existing user and is only ever populated one key at a time, by
   an explicit grant/revoke through POST /users/{id}/permissions.

2. audit_logs.action_category / resource_type / resource_id (all NULL) —
   structured Activity History filtering columns, additive alongside the
   existing business_day_id/metadata_json columns from earlier migrations.
   Existing rows are NOT backfilled — Activity History is append-only, and a
   historical row's category is data this migration cannot honestly
   reconstruct, so old rows simply read as NULL category/resource going
   forward (list_audit_logs handles that: a category filter naturally
   excludes them, exactly like a keyword filter would).

3. presence_sessions.last_activity_at (TIMESTAMP, NULL) — distinct from the
   existing last_seen_at (heartbeat/connectivity proof): this is the last
   time genuine user interaction was observed, used to derive the
   Online/Inactive split. Backfilled to signed_in_at only for EXISTING rows
   (so an already-open historical session doesn't read as "never active");
   every new row going forward gets a real value from the first heartbeat
   that reports activity.

Revision ID: 0022_permissions_activity_presence
Revises: 0021_product_updated_at
"""
from alembic import op
import sqlalchemy as sa

revision = "0022_permissions_activity_presence"
down_revision = "0021_product_updated_at"
branch_labels = None
depends_on = None


def _has_column(bind, table: str, column: str) -> bool:
    return column in {c["name"] for c in sa.inspect(bind).get_columns(table)}


def _has_index(bind, table: str, index_name: str) -> bool:
    return index_name in {ix["name"] for ix in sa.inspect(bind).get_indexes(table)}


def upgrade() -> None:
    bind = op.get_bind()

    if not _has_column(bind, "users", "permission_overrides"):
        op.add_column("users", sa.Column("permission_overrides", sa.Text(), nullable=True))

    if not _has_column(bind, "audit_logs", "action_category"):
        op.add_column("audit_logs", sa.Column("action_category", sa.String(), nullable=True))
    if not _has_column(bind, "audit_logs", "resource_type"):
        op.add_column("audit_logs", sa.Column("resource_type", sa.String(), nullable=True))
    if not _has_column(bind, "audit_logs", "resource_id"):
        op.add_column("audit_logs", sa.Column("resource_id", sa.Integer(), nullable=True))
    if not _has_index(bind, "audit_logs", "ix_audit_logs_business_category_created"):
        op.create_index(
            "ix_audit_logs_business_category_created", "audit_logs",
            ["business_id", "action_category", "created_at"],
        )
    if not _has_index(bind, "audit_logs", "ix_audit_logs_business_actor"):
        op.create_index("ix_audit_logs_business_actor", "audit_logs", ["business_id", "actor_user_id"])

    if not _has_column(bind, "presence_sessions", "last_activity_at"):
        op.add_column("presence_sessions", sa.Column("last_activity_at", sa.DateTime(), nullable=True))
        # Backfill EXISTING rows only, to their own signed_in_at (the one
        # honest "last known activity" fact already on the row) — never to
        # last_seen_at (that's a heartbeat/connectivity signal, not activity)
        # and never applied to rows created after this column exists, which
        # already populate it correctly at write time.
        op.execute("UPDATE presence_sessions SET last_activity_at = signed_in_at WHERE last_activity_at IS NULL")


def downgrade() -> None:
    bind = op.get_bind()
    if _has_index(bind, "audit_logs", "ix_audit_logs_business_actor"):
        op.drop_index("ix_audit_logs_business_actor", table_name="audit_logs")
    if _has_index(bind, "audit_logs", "ix_audit_logs_business_category_created"):
        op.drop_index("ix_audit_logs_business_category_created", table_name="audit_logs")
    if _has_column(bind, "presence_sessions", "last_activity_at"):
        op.drop_column("presence_sessions", "last_activity_at")
    if _has_column(bind, "audit_logs", "resource_id"):
        op.drop_column("audit_logs", "resource_id")
    if _has_column(bind, "audit_logs", "resource_type"):
        op.drop_column("audit_logs", "resource_type")
    if _has_column(bind, "audit_logs", "action_category"):
        op.drop_column("audit_logs", "action_category")
    if _has_column(bind, "users", "permission_overrides"):
        op.drop_column("users", "permission_overrides")
