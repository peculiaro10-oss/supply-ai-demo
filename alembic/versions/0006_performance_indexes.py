"""Performance indexes reconciliation.

The application's own SQLite dev-mode auto-migration path (main.py, gated
by AUTO_CREATE_SCHEMA) has created these 11 indexes on every local/test
database since the earlier performance-refactor work, but they were never
captured in an Alembic migration — so production PostgreSQL (which runs
with AUTO_CREATE_SCHEMA=false and is Alembic-managed) never received them.
This migration closes that gap. Confirmed missing via a live audit of the
production schema (see the schema reconciliation report) before writing
this migration — nothing here is guessed.

Purely additive and non-destructive: CREATE INDEX IF NOT EXISTS only, no
column or table changes, no data touched. Every index matches an existing
filter/GROUP BY pattern already used by the sales/financial-intelligence/
business-brain/audit endpoints (see main.py) — this does not add new query
patterns, it just gives already-existing ones an index to use.

Revision ID: 0006_performance_indexes
Revises: 0005_sale_cost_snapshot
"""
from alembic import op

revision = "0006_performance_indexes"
down_revision = "0005_sale_cost_snapshot"
branch_labels = None
depends_on = None

INDEXES = [
    ("ix_sales_business_timestamp", "sales", "(business_id, timestamp)"),
    ("ix_sales_business_day", "sales", "(business_id, business_day_id)"),
    ("ix_sales_product", "sales", "(product_id)"),
    ("ix_expenses_business_created", "expenses", "(business_id, created_at)"),
    ("ix_business_days_business_closed", "business_days", "(business_id, closed_at)"),
    ("ix_business_days_business_open", "business_days", "(business_id, is_open)"),
    ("ix_audit_logs_business_created", "audit_logs", "(business_id, created_at)"),
    ("ix_audit_logs_business_day", "audit_logs", "(business_day_id)"),
    ("ix_alerts_business_created", "alerts", "(business_id, created_at)"),
    ("ix_reopen_requests_business_status", "business_day_reopen_requests", "(business_id, status)"),
    ("ix_account_action_requests_business_status", "account_action_requests", "(business_id, status)"),
]


def upgrade() -> None:
    for name, table, cols in INDEXES:
        op.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table} {cols}")


def downgrade() -> None:
    for name, _table, _cols in INDEXES:
        op.execute(f"DROP INDEX IF EXISTS {name}")
