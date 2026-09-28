"""Subscription renewal engine and reminder log (SUB-LIFECYCLE-001).

Paystack Subscriptions never retry a failed charge ("Subscriptions aren't
retried") and keep charging the old subscription on its next payment date, so
Cauldra becomes the single renewal engine (Charge Authorization, one attempt
per 12-hour slot for 3 days from the exact paid-through time). This adds:

1. payment_records
   - renewal_period_end: the paid-through time this payment renews. At most one
     SUCCESSFUL payment per business and renewal_period_end (partial unique
     index), so two devices or engines can never both extend one period; a
     second confirmed payment is kept as a duplicate-payment exception.
   - attempt_source ('auto' | 'manual') and renewal_attempt_slot (0..5): at most
     one automatic attempt per business, period and 12-hour slot.
2. business_subscriptions
   - paystack_authorization_email: the customer email Paystack bound to the
     saved reusable authorization (needed by Charge Authorization).
   - paused_from / resumed_at: the last pause interval, so offline work captured
     while the subscription was paused is refused (kept for review), not applied.
3. subscription_reminder_deliveries: one row per business, period, stage,
   channel and recipient - the durable "sent" log for in-app and email reminders.

Additive and nullable only: older code keeps working against the upgraded
schema, so this runs BEFORE the code deploy. Safe to run more than once.

Revision ID: 0044_subscription_renewal_engine
Revises: 0043_ops_accuracy_telemetry
"""
from alembic import op
import sqlalchemy as sa

revision = "0044_subscription_renewal_engine"
down_revision = "0043_ops_accuracy_telemetry"
branch_labels = None
depends_on = None

PAYMENT_COLUMNS = (
    ("renewal_period_end", sa.DateTime()),
    ("attempt_source", sa.String()),
    ("renewal_attempt_slot", sa.Integer()),
)
SUBSCRIPTION_COLUMNS = (
    ("paystack_authorization_email", sa.String()),
    ("paused_from", sa.DateTime()),
    ("resumed_at", sa.DateTime()),
)


def _inspector():
    return sa.inspect(op.get_bind())


def _columns(table):
    return {c["name"] for c in _inspector().get_columns(table)}


def _indexes(table):
    return {i["name"] for i in _inspector().get_indexes(table)}


def upgrade():
    existing = _columns("payment_records")
    for name, type_ in PAYMENT_COLUMNS:
        if name not in existing:
            op.add_column("payment_records", sa.Column(name, type_, nullable=True))
    existing = _columns("business_subscriptions")
    for name, type_ in SUBSCRIPTION_COLUMNS:
        if name not in existing:
            op.add_column("business_subscriptions", sa.Column(name, type_, nullable=True))

    indexes = _indexes("payment_records")
    if "uq_payment_records_one_success_per_renewal" not in indexes:
        op.create_index(
            "uq_payment_records_one_success_per_renewal", "payment_records",
            ["business_id", "renewal_period_end"], unique=True,
            postgresql_where=sa.text("status = 'success' AND renewal_period_end IS NOT NULL"),
            sqlite_where=sa.text("status = 'success' AND renewal_period_end IS NOT NULL"),
        )
    if "uq_payment_records_one_auto_attempt_per_slot" not in indexes:
        op.create_index(
            "uq_payment_records_one_auto_attempt_per_slot", "payment_records",
            ["business_id", "renewal_period_end", "renewal_attempt_slot"], unique=True,
            postgresql_where=sa.text("attempt_source = 'auto'"),
            sqlite_where=sa.text("attempt_source = 'auto'"),
        )

    if "subscription_reminder_deliveries" not in _inspector().get_table_names():
        op.create_table(
            "subscription_reminder_deliveries",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("business_id", sa.Integer(), sa.ForeignKey("business_profile.id", ondelete="CASCADE"), nullable=False),
            sa.Column("period_anchor", sa.DateTime(), nullable=False),
            sa.Column("stage", sa.String(), nullable=False),
            sa.Column("channel", sa.String(), nullable=False),
            sa.Column("recipient", sa.String(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("last_error_category", sa.String(), nullable=True),
            sa.Column("sent_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("business_id", "period_anchor", "stage", "channel", "recipient",
                                name="uq_subscription_reminder_delivery"),
        )
        op.create_index("ix_subscription_reminder_deliveries_business_id", "subscription_reminder_deliveries", ["business_id"])
        op.create_index("ix_subscription_reminder_deliveries_status", "subscription_reminder_deliveries", ["status"])


def downgrade():
    if "subscription_reminder_deliveries" in _inspector().get_table_names():
        op.drop_table("subscription_reminder_deliveries")
    indexes = _indexes("payment_records")
    for name in ("uq_payment_records_one_auto_attempt_per_slot", "uq_payment_records_one_success_per_renewal"):
        if name in indexes:
            op.drop_index(name, table_name="payment_records")
    existing = _columns("business_subscriptions")
    for name, _ in reversed(SUBSCRIPTION_COLUMNS):
        if name in existing:
            op.drop_column("business_subscriptions", name)
    existing = _columns("payment_records")
    for name, _ in reversed(PAYMENT_COLUMNS):
        if name in existing:
            op.drop_column("payment_records", name)
