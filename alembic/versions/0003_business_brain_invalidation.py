"""Business Brain invalidation columns on business_profile.

Adds:
- business_profile.business_brain_dirty: marks a business's stored Business
  Brain recommendations/predictions as needing a refresh. GET /business-brain
  only recomputes when this is true (see main.py's business_brain() /
  mark_business_brain_dirty() / _try_claim_business_brain_refresh()) instead
  of running the full per-product analytical pass on every read. Defaults
  true so every existing business gets exactly one correct refresh the first
  time it's read after this migration, rather than silently serving a
  never-computed state.
- business_profile.business_brain_refreshed_at: when the stored state was
  last actually refreshed. Nullable — no meaningful value exists until the
  first refresh runs.

Fixes: psycopg2.errors.UndefinedColumn on business_profile.business_brain_dirty
/ business_profile.business_brain_refreshed_at. The SQLAlchemy model was
updated for the Business Brain performance refactor (see main.py) without a
matching migration for the production PostgreSQL schema, so every
BusinessProfile SELECT — including Business ID verification and /auth/me —
started failing with that missing-column error.

This is purely additive: no table is dropped or recreated, no existing row
is modified beyond receiving the new column's default value, and no
business/user/product/sale/audit/subscription data is touched.

Revision ID: 0003_business_brain_invalidation
Revises: 0002_business_day_integrity
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_business_brain_invalidation"
down_revision = "0002_business_day_integrity"
branch_labels = None
depends_on = None


def _add_column_if_missing(bind, table_name: str, column: sa.Column):
    inspector = sa.inspect(bind)
    existing = {c["name"] for c in inspector.get_columns(table_name)}
    if column.name not in existing:
        op.add_column(table_name, column)


def upgrade() -> None:
    bind = op.get_bind()
    _add_column_if_missing(
        bind, "business_profile",
        sa.Column("business_brain_dirty", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    _add_column_if_missing(
        bind, "business_profile",
        sa.Column("business_brain_refreshed_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    # Unlike the Business Day integrity migration, these are purely derived/
    # cache-style columns (never user-entered data, never an audit record) —
    # safe to drop without losing anything meaningful. Guarded the same way
    # upgrade() is, so downgrading a database that never had them (or that
    # already had them dropped) is a no-op rather than an error.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {c["name"] for c in inspector.get_columns("business_profile")}
    if "business_brain_refreshed_at" in existing:
        op.drop_column("business_profile", "business_brain_refreshed_at")
    if "business_brain_dirty" in existing:
        op.drop_column("business_profile", "business_brain_dirty")
