"""Notification system: notifications, push_subscriptions, notification_preferences.

Adds Cauldra's notification center + external push delivery persistence
layer. See main.py's Notification/PushSubscription/NotificationPreference
model docstrings, and create_notification()/deliver_push_notification() for
the engine that owns writes to these tables.

Purely additive: three brand-new tables (created via Base.metadata.create_all
with checkfirst=True, the same pattern 0002_business_day_integrity and
0007_refunds used for their own new tables) plus supporting indexes. No
existing table, column, or row is touched.

Revision ID: 0010_notifications
Revises: 0009_sale_transaction_header
"""
from alembic import op
import sqlalchemy as sa

revision = "0010_notifications"
down_revision = "0009_sale_transaction_header"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    from main import Base
    Base.metadata.create_all(
        bind=bind, checkfirst=True,
        tables=[
            Base.metadata.tables["notifications"],
            Base.metadata.tables["push_subscriptions"],
            Base.metadata.tables["notification_preferences"],
        ],
    )

    # Notification center list/unread-count queries always filter by
    # recipient first, then by read state or dedup lookups.
    op.execute("CREATE INDEX IF NOT EXISTS ix_notifications_recipient_created ON notifications (recipient_user_id, created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_notifications_recipient_unread ON notifications (recipient_user_id, is_read)")
    # Dedup lookup: "does an unresolved notification with this dedup_key for
    # this recipient already exist" — see create_notification().
    op.execute("CREATE INDEX IF NOT EXISTS ix_notifications_dedup_lookup ON notifications (recipient_user_id, dedup_key, resolved_at)")
    # Push delivery only ever queries a user's active (non-disabled) subscriptions.
    op.execute("CREATE INDEX IF NOT EXISTS ix_push_subscriptions_user_active ON push_subscriptions (user_id, disabled_at)")


def downgrade() -> None:
    # Notifications are a user-visible history/audit-adjacent record —
    # refuse to drop them automatically, matching this app's existing policy
    # for financial/audit data (see 0007_refunds.downgrade()).
    raise RuntimeError("Refusing to drop notification tables/data automatically.")
