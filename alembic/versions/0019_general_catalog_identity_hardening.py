"""General Catalog identity hardening: defense-in-depth unique indexes.

Purely defensive/additive — no column is added or removed, and no row is
touched, moved, or deleted. general_catalog.barcode and .catalog_key have
carried `unique=True` on the SQLAlchemy model since GeneralCatalog was first
introduced (see main.py's GeneralCatalog class, and 0001_baseline_schema.py,
which creates the whole schema from that same model via
Base.metadata.create_all()), so a real deployment almost certainly already
has both unique indexes. This migration only GUARANTEES it, idempotently,
because the application's new atomic upsert
(_general_catalog_atomic_get_or_create() in main.py) issues a PostgreSQL
`INSERT ... ON CONFLICT (barcode | catalog_key) DO NOTHING` — and Postgres
requires a real unique index/constraint on the named column for that clause
to be valid at all. If the index were somehow missing, that statement would
fail LOUDLY (a clear startup/runtime error) rather than silently degrading
into duplicate rows — this migration exists purely to make that failure
mode impossible, at zero cost when the index already exists.

Revision ID: 0019_general_catalog_identity_hardening
Revises: 0018_platform_owner_control_panel

--------------------------------------------------------------------------
NOT part of this migration, by design — a READ-ONLY diagnostic for a human
to run and judge by hand, never something an automated migration should
decide on its own (see the "do not blindly merge/delete user-derived
catalog knowledge" rule this feature was built under):

No-barcode General Catalog identity used to be `name + category`; this
release moves it to `name + normalized size` (category dropped entirely —
see GeneralCatalog's class docstring and general_catalog_key_for() in
main.py for why). Existing no-barcode rows keep their OLD catalog_key value
untouched (this migration does not rewrite it), so they simply stop being
matched by NEW lookups going forward — they are not deleted, corrupted, or
merged, just inert going forward. To manually look for pre-existing rows
that might describe the same physical product under the old scheme (and
decide case-by-case whether to hand-merge any of them):

    SELECT catalog_key, product_name, size, brand, source, created_at
    FROM general_catalog
    WHERE barcode IS NULL
    ORDER BY lower(trim(product_name)), size;

Group the output by (lower(trim(product_name)), size) yourself and look for
rows that clearly describe the same physical product before deciding
whether to hand-merge any of them. Never merge purely on the OLD
category-based key looking similar — category was never reliable shared
identity in the first place (that is the entire reason this migration
exists).
--------------------------------------------------------------------------
"""
from alembic import op
import sqlalchemy as sa

revision = "0019_general_catalog_identity_hardening"
down_revision = "0018_platform_owner_control_panel"
branch_labels = None
depends_on = None


def _has_unique_index_or_constraint(bind, table_name: str, column: str) -> bool:
    inspector = sa.inspect(bind)
    for index in inspector.get_indexes(table_name):
        if index.get("unique") and list(index.get("column_names") or []) == [column]:
            return True
    for constraint in inspector.get_unique_constraints(table_name):
        if list(constraint.get("column_names") or []) == [column]:
            return True
    pk = inspector.get_pk_constraint(table_name) or {}
    if list(pk.get("constrained_columns") or []) == [column]:
        return True
    return False


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_unique_index_or_constraint(bind, "general_catalog", "barcode"):
        op.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_general_catalog_barcode_defense "
            "ON general_catalog (barcode)"
        )
    if not _has_unique_index_or_constraint(bind, "general_catalog", "catalog_key"):
        op.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_general_catalog_catalog_key_defense "
            "ON general_catalog (catalog_key)"
        )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ux_general_catalog_catalog_key_defense")
    op.execute("DROP INDEX IF EXISTS ux_general_catalog_barcode_defense")
