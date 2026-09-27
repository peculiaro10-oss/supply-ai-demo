"""Private Ops accuracy (OPS-ACCURACY-001): two small, additive telemetry fields.

1. ai_usage_ledger.failure_category - why a failed AI request failed
   (provider_unavailable, rate_limited, insufficient_provider_credit, timeout,
   malformed_response, validation_failure, other). Rows written before this
   migration keep NULL, which Ops shows as "Reason not recorded".
2. platform_alerts gains what a grouped, plain-English operational inbox needs:
   source, state (unresolved / watching / resolved), occurrences, last_seen_at,
   impact, why_it_matters, technical_detail, affected_businesses, resolved_at,
   resolved_by_id, external_url. Existing alerts keep NULLs and read as one
   occurrence from Cauldra, "resolved" when already acknowledged.

Additive and nullable only: older code keeps working against the upgraded
schema, so this runs BEFORE the code deploy. Safe to run more than once.

Revision ID: 0043_ops_accuracy_telemetry
Revises: 0042_business_brain_forecast_recompute
"""
from alembic import op
import sqlalchemy as sa

revision = "0043_ops_accuracy_telemetry"
down_revision = "0042_business_brain_forecast_recompute"
branch_labels = None
depends_on = None

ALERT_COLUMNS = (
    ("source", sa.String()),
    ("state", sa.String()),
    ("occurrences", sa.Integer()),
    ("last_seen_at", sa.DateTime()),
    ("impact", sa.Text()),
    ("why_it_matters", sa.Text()),
    ("technical_detail", sa.Text()),
    ("affected_businesses", sa.Integer()),
    ("resolved_at", sa.DateTime()),
    ("resolved_by_id", sa.Integer()),
    ("external_url", sa.String()),
)


def _columns(table):
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade():
    if "failure_category" not in _columns("ai_usage_ledger"):
        op.add_column("ai_usage_ledger", sa.Column("failure_category", sa.String(), nullable=True))
    existing = _columns("platform_alerts")
    for name, type_ in ALERT_COLUMNS:
        if name not in existing:
            op.add_column("platform_alerts", sa.Column(name, type_, nullable=True))


def downgrade():
    existing = _columns("platform_alerts")
    for name, _ in reversed(ALERT_COLUMNS):
        if name in existing:
            op.drop_column("platform_alerts", name)
    if "failure_category" in _columns("ai_usage_ledger"):
        op.drop_column("ai_usage_ledger", "failure_category")
