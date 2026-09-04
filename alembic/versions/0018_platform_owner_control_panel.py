"""Cauldra Platform Owner Control Panel (V31): six new, fully separate tables
for internal Cauldra-level auth/analytics, plus two new columns on `users`
for platform-wide activity tracking.

WHY SEPARATE TABLES, NOT A REUSE OF EXISTING ONES
--------------------------------------------------
platform_owners / platform_owner_session_revocations / platform_audit_logs /
platform_alerts / ai_provider_pricing / platform_settings carry NO foreign key
to `users` or `business_profile`, and nothing in `users`/`business_profile`
points at them either. A Platform Owner is not a business's Customer Admin
(see main.py's PLATFORM CONTROL PANEL section for the full authorization
argument) - it is deliberately impossible to reach a platform_owners row by
traversing from customer data, or vice versa.

- platform_audit_logs is NOT the existing `audit_logs` table: that table
  requires a business_id (add_audit() returns early without one) and every
  existing reader of it is business-scoped; a platform-level action has no
  single business to attach to.
- platform_alerts is NOT the existing `notifications` table: that table's
  business_id/recipient_user_id are both NOT NULL, scoped to one business's
  own users; a platform-wide alert (e.g. "OpenAI spend at 85% of budget") has
  no single business or recipient.
- ai_provider_pricing / platform_settings are new because no existing table
  stores a $/1,000-token provider rate or an internal monitoring budget -
  `ai_usage_ledger` (unchanged by this migration) already had the columns to
  RECORD usage/cost; this migration doesn't touch it, it only creates the
  configuration these new endpoints read to fill those columns in for the
  first time (see main.py's estimate_ai_cost_usd()).

WHY users.created_at / users.last_active_at ARE ADDITIVE, NOT BACKFILLED
--------------------------------------------------------------------------
Both are nullable, no backfill. An existing user's real join date is
genuinely unknown - writing "now" would be fabricated data (the platform
dashboard explicitly must never show invented activity/growth numbers).
last_active_at starts NULL for everyone and is populated going forward by the
same already-existing, already-throttled call sites (login,
POST /presence/heartbeat - unchanged cadence, ~once/60s while the app is
open) - no new write path, no new frontend polling.

Uses the SAME idempotent, models-are-the-schema pattern as 0001/0013/0017:
Base.metadata.create_all(checkfirst=True) for the brand-new tables (a no-op
if they already exist, e.g. a database created from a newer models.py), and
_add_column_if_missing-style guards for the two new `users` columns.

Revision ID: 0018_platform_owner_control_panel
Revises: 0017_user_profile_fields
"""
from alembic import op
import sqlalchemy as sa

revision = "0018_platform_owner_control_panel"
down_revision = "0017_user_profile_fields"
branch_labels = None
depends_on = None

_NEW_TABLES = [
    "platform_owners",
    "platform_owner_session_revocations",
    "platform_audit_logs",
    "platform_alerts",
    "ai_provider_pricing",
    "platform_settings",
]

_NEW_USER_COLUMNS = (
    ("created_at", sa.DateTime),
    ("last_active_at", sa.DateTime),
)

# platform_owners.email_verified_at ships with the table itself when the
# table is brand-new (the normal case - see upgrade()), but is added here as
# a guarded ALTER too so a database that already has platform_owners from an
# earlier partial run of this migration still ends up complete.
_NEW_PLATFORM_OWNER_COLUMNS = (
    ("email_verified_at", sa.DateTime),
)


def upgrade() -> None:
    from main import Base

    bind = op.get_bind()
    inspector = sa.inspect(bind)

    tables = [Base.metadata.tables[name] for name in _NEW_TABLES if name in Base.metadata.tables]
    Base.metadata.create_all(bind=bind, checkfirst=True, tables=tables)

    existing_user_cols = {c["name"] for c in inspector.get_columns("users")}
    for name, coltype in _NEW_USER_COLUMNS:
        if name not in existing_user_cols:
            op.add_column("users", sa.Column(name, coltype(), nullable=True))

    if "platform_owners" in set(inspector.get_table_names()):
        existing_owner_cols = {c["name"] for c in inspector.get_columns("platform_owners")}
        for name, coltype in _NEW_PLATFORM_OWNER_COLUMNS:
            if name not in existing_owner_cols:
                op.add_column("platform_owners", sa.Column(name, coltype(), nullable=True))

    # create_all() creates indexes for brand-new TABLES automatically, but
    # op.add_column() on an existing table does not - these two are declared
    # index=True on the User model, so create the matching indexes explicitly.
    op.execute("CREATE INDEX IF NOT EXISTS ix_users_created_at ON users (created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_users_last_active_at ON users (last_active_at)")


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    op.execute("DROP INDEX IF EXISTS ix_users_last_active_at")
    op.execute("DROP INDEX IF EXISTS ix_users_created_at")

    existing_user_cols = {c["name"] for c in inspector.get_columns("users")}
    for name, _coltype in reversed(_NEW_USER_COLUMNS):
        if name in existing_user_cols:
            op.drop_column("users", name)

    if "platform_owners" in set(inspector.get_table_names()):
        existing_owner_cols = {c["name"] for c in inspector.get_columns("platform_owners")}
        for name, _coltype in reversed(_NEW_PLATFORM_OWNER_COLUMNS):
            if name in existing_owner_cols:
                op.drop_column("platform_owners", name)

    existing_tables = set(inspector.get_table_names())
    for name in reversed(_NEW_TABLES):
        if name in existing_tables:
            op.execute(f'DROP TABLE IF EXISTS "{name}" CASCADE')
