"""At-most-one-active-Business-Day-PER-LOCATION (multi-location operation).

Prior to this migration, `ux_business_days_one_active` (migration 0004)
enforced at most one OPEN Business Day for the ENTIRE business, regardless
of location. That was correct for a single-location business, but is wrong
once a business has more than one active Location: Lagos HQ and London
Branch must be able to have their own simultaneously open sessions without
colliding with each other (see main.py's start_business_day() / section 13
of the final completion pass).

This migration:
1. Backfills `location_id` on any EXISTING row that is CURRENTLY OPEN
   (is_open = true) but still has location_id = NULL, to that business's
   Main Location. This is the ONE deliberate exception to migration 0024's
   "never backfill an existing BusinessDay.location_id" rule — it applies
   ONLY to rows that are open right now, and ONLY so the new constraint
   below is meaningful for them. Every CLOSED historical row is left
   untouched, exactly as 0024 already established (NULL there keeps
   meaning "the business's Main Location, by convention" forever).
2. Drops the old business-wide `ux_business_days_one_active` index.
3. Creates `ux_business_days_one_active_per_location` — a partial unique
   index on (business_id, location_id) WHERE is_open = true. Going
   forward, main.py's start_business_day()/_create_business_day_session()
   ALWAYS resolve a concrete location_id before opening a new session
   (never NULL — see resolve_business_day_location()), so this index is
   the true database-level backstop against two concurrent opens racing
   for the SAME location, exactly as the old index was for the
   single-location case.

Idempotent and safe to run more than once.

Revision ID: 0025_business_day_per_location
Revises: 0024_locations
"""
from alembic import op
import sqlalchemy as sa

revision = "0025_business_day_per_location"
down_revision = "0024_locations"
branch_labels = None
depends_on = None


def _has_index(bind, table: str, index: str) -> bool:
    return index in {ix["name"] for ix in sa.inspect(bind).get_indexes(table)}


def upgrade() -> None:
    bind = op.get_bind()

    # 1. Backfill location_id ONLY for rows that are open right now and
    # still NULL — scoped per-business via a correlated FROM, so a
    # business's currently-open session can only ever be backfilled to that
    # SAME business's own Main Location, never another business's.
    bind.execute(sa.text("""
        UPDATE business_days bd
        SET location_id = l.id
        FROM locations l
        WHERE l.business_id = bd.business_id AND l.is_main = TRUE
          AND bd.is_open = TRUE AND bd.location_id IS NULL
    """))

    # 2. Drop the old business-wide active-day uniqueness index.
    if _has_index(bind, "business_days", "ux_business_days_one_active"):
        op.execute("DROP INDEX IF EXISTS ux_business_days_one_active")

    # 3. Create the new per-location uniqueness index.
    if not _has_index(bind, "business_days", "ux_business_days_one_active_per_location"):
        op.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_business_days_one_active_per_location "
            "ON business_days (business_id, location_id) WHERE is_open = true"
        )


def downgrade() -> None:
    bind = op.get_bind()
    op.execute("DROP INDEX IF EXISTS ux_business_days_one_active_per_location")
    if not _has_index(bind, "business_days", "ux_business_days_one_active"):
        # Best-effort restore of the original business-wide index. If more
        # than one Location is genuinely open at the moment of downgrade,
        # this CREATE will fail (correctly) rather than silently discarding
        # one of the two active sessions — resolve that data state manually
        # (close every location's day but one) before downgrading.
        op.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_business_days_one_active "
            "ON business_days (business_id) WHERE is_open = true"
        )
