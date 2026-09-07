"""Multi-location / multi-country business architecture.

ONE Business Profile has MANY Locations; a Location owns Warehouses. This
migration is purely additive and safely backfilling — it does not touch or
remove any existing BusinessProfile column (they remain in place; see the
Location model's docstring in main.py for why), and it never orphans an
existing warehouse or Business Day session.

What this does, in order:
1. Creates the `locations` table.
2. Creates ONE "Main Location" per EXISTING business, populated from that
   business's own current BusinessProfile country/country_code/timezone/
   currency/phone/email/address — never fabricated, never guessed from
   free text (no city is invented if BusinessProfile has none).
3. Adds `warehouses.location_id` (nullable FK, ondelete=SET NULL) and
   reassigns every existing warehouse to its business's new Main Location.
4. Adds `business_days.location_id` (nullable FK, ondelete=SET NULL) —
   left NULL for every existing session (an existing closed/open session
   predates location-awareness and NULL there means "the business's Main
   Location" by convention, exactly as main.py's BusinessDay model
   docstring says); only NEW sessions going forward get stamped by
   start_business_day()/_create_business_day_session().

Idempotent: every step checks for its own prior existence first, so this
migration is safe to run more than once and safe on a database where some
of this may already be partially present.

Revision ID: 0024_locations
Revises: 0023_purchase_order_requirements
"""
from alembic import op
import sqlalchemy as sa

revision = "0024_locations"
down_revision = "0023_purchase_order_requirements"
branch_labels = None
depends_on = None


def _has_table(bind, table: str) -> bool:
    return table in sa.inspect(bind).get_table_names()


def _has_column(bind, table: str, column: str) -> bool:
    return column in {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()

    if not _has_table(bind, "locations"):
        op.create_table(
            "locations",
            sa.Column("id", sa.Integer(), primary_key=True, index=True),
            sa.Column("business_id", sa.Integer(), sa.ForeignKey("business_profile.id", ondelete="CASCADE"), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("country", sa.String(), nullable=True),
            sa.Column("country_code", sa.String(), nullable=True),
            sa.Column("city", sa.String(), nullable=True),
            sa.Column("timezone", sa.String(), nullable=True),
            sa.Column("currency", sa.String(), nullable=True),
            sa.Column("contact_phone", sa.String(), nullable=True),
            sa.Column("contact_email", sa.String(), nullable=True),
            sa.Column("address", sa.String(), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("is_main", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
        op.create_index("ix_locations_business", "locations", ["business_id"])

    # One Main Location per EXISTING business that doesn't already have one
    # (idempotent — a rerun, or a business that already got one from a
    # partial prior run, is skipped). INSERT ... SELECT so this scales to
    # however many businesses exist without loading them into Python.
    bind.execute(sa.text("""
        INSERT INTO locations (business_id, name, country, country_code, timezone, currency, contact_phone, contact_email, address, is_active, is_main, created_at, updated_at)
        SELECT bp.id, 'Main Location', bp.country, bp.country_code, bp.timezone, bp.currency, bp.phone, bp.email, bp.address, TRUE, TRUE, NOW(), NOW()
        FROM business_profile bp
        WHERE NOT EXISTS (SELECT 1 FROM locations l WHERE l.business_id = bp.id AND l.is_main = TRUE)
    """))

    if not _has_column(bind, "warehouses", "location_id"):
        op.add_column("warehouses", sa.Column("location_id", sa.Integer(), sa.ForeignKey("locations.id", ondelete="SET NULL"), nullable=True))
        op.create_index("ix_warehouses_location", "warehouses", ["location_id"])
    # Reassign every warehouse that has no location yet to its OWN
    # business's Main Location — scoped by business_id so this can never
    # cross-assign a warehouse to a different business's location.
    bind.execute(sa.text("""
        UPDATE warehouses w
        SET location_id = l.id
        FROM locations l
        WHERE l.business_id = w.business_id AND l.is_main = TRUE AND w.location_id IS NULL
    """))

    if not _has_column(bind, "business_days", "location_id"):
        op.add_column("business_days", sa.Column("location_id", sa.Integer(), sa.ForeignKey("locations.id", ondelete="SET NULL"), nullable=True))
        op.create_index("ix_business_days_location", "business_days", ["location_id"])
    # Existing sessions are deliberately left location_id = NULL (see module
    # docstring) — NOT backfilled to Main Location — because NULL already
    # means exactly that by the application's own convention, and an
    # explicit backfill here would be indistinguishable from a genuinely
    # multi-location business's legacy data later.


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, "business_days", "location_id"):
        op.drop_column("business_days", "location_id")
    if _has_column(bind, "warehouses", "location_id"):
        op.drop_column("warehouses", "location_id")
    if _has_table(bind, "locations"):
        op.drop_table("locations")
