"""Push device ownership and native Android push (NOTIF-PUSH-001, NOTIF-PUSH-002).

1. refresh_sessions.family_id - one id per sign-in, carried across every
   refresh rotation, so a push registration can be bound to the sign-in that
   made it and stop receiving pushes when that sign-in ends.
2. push_subscriptions (Web Push): session_family, auth_version, revoked_at,
   revoked_reason - the ownership of each browser registration.
3. native_push_devices: one row per installed Android app's Firebase Cloud
   Messaging token, with the same ownership columns.

Additive and nullable only: older code keeps working against the upgraded
schema, so this runs BEFORE the code deploy. Existing Web Push rows have no
session_family, so the new code treats them as unbound (never pushed) until
the browser re-registers from a live sign-in. Safe to run more than once.

Revision ID: 0045_push_device_ownership
Revises: 0044_subscription_renewal_engine
"""
from alembic import op
import sqlalchemy as sa

revision = "0045_push_device_ownership"
down_revision = "0044_subscription_renewal_engine"
branch_labels = None
depends_on = None

PUSH_COLUMNS = (
    ("session_family", sa.String()),
    ("auth_version", sa.Integer()),
    ("revoked_at", sa.DateTime()),
    ("revoked_reason", sa.String()),
)


def _inspector():
    return sa.inspect(op.get_bind())


def _columns(table):
    return {c["name"] for c in _inspector().get_columns(table)}


def _indexes(table):
    return {i["name"] for i in _inspector().get_indexes(table)}


def upgrade():
    if "family_id" not in _columns("refresh_sessions"):
        op.add_column("refresh_sessions", sa.Column("family_id", sa.String(), nullable=True))
    if "ix_refresh_sessions_family_id" not in _indexes("refresh_sessions"):
        op.create_index("ix_refresh_sessions_family_id", "refresh_sessions", ["family_id"])

    existing = _columns("push_subscriptions")
    for name, type_ in PUSH_COLUMNS:
        if name not in existing:
            op.add_column("push_subscriptions", sa.Column(name, type_, nullable=True))
    if "ix_push_subscriptions_session_family" not in _indexes("push_subscriptions"):
        op.create_index("ix_push_subscriptions_session_family", "push_subscriptions", ["session_family"])

    if not _inspector().has_table("native_push_devices"):
        op.create_table(
            "native_push_devices",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("business_id", sa.Integer(), sa.ForeignKey("business_profile.id", ondelete="CASCADE"), nullable=False),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("platform", sa.String(), nullable=False),
            sa.Column("token", sa.Text(), nullable=False, unique=True),
            sa.Column("app_version", sa.String(), nullable=True),
            sa.Column("session_family", sa.String(), nullable=True),
            sa.Column("auth_version", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("last_seen_at", sa.DateTime(), nullable=False),
            sa.Column("disabled_at", sa.DateTime(), nullable=True),
            sa.Column("revoked_at", sa.DateTime(), nullable=True),
            sa.Column("revoked_reason", sa.String(), nullable=True),
        )
    indexes = _indexes("native_push_devices")
    for name, cols in (("ix_native_push_devices_id", ["id"]), ("ix_native_push_devices_business_id", ["business_id"]),
                       ("ix_native_push_devices_user_id", ["user_id"]), ("ix_native_push_devices_session_family", ["session_family"])):
        if name not in indexes:
            op.create_index(name, "native_push_devices", cols)


def downgrade():
    raise RuntimeError("Refusing to drop push ownership data automatically.")
