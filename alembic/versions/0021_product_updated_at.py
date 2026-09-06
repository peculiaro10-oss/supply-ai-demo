"""Product.updated_at — optimistic-concurrency guard for offline-sync edits.

Adds one new nullable column, `products.updated_at`. No existing column,
table, or row is dropped, renamed, or destroyed, and no backfill is
performed (see reasoning below) — this is purely additive.

Why this column exists: the offline-first sync engine can replay a queued
product edit made while the device was disconnected, possibly well after it
was queued. Without a way to detect that the product changed server-side in
the meantime (another device, another queued replay, or an online edit by a
different user), a stale offline edit could silently overwrite newer data.
`update_product()` in main.py now accepts an optional `base_updated_at` in
the request body — the value the client last saw — and rejects the update
with 409 if the row has moved on since, rather than blindly applying it.

No backfill for existing rows: `updated_at` is set going forward by
SQLAlchemy's `onupdate=datetime.utcnow` the next time each row is actually
edited (see the Product model in main.py). An existing product with no prior
edit since this migration simply has `updated_at IS NULL` until its first
edit after this deploy — and the concurrency check in update_product() is
written to skip itself whenever either side is NULL (a client with no
base_updated_at to send, or a product that has never been touched since this
column existed), so this is never treated as a false conflict. Backfilling
every existing row to `created_at` (as 0020 did for `purchase_orders.sent_at`,
where every row already had a real "sent" moment worth recording) would be
actively misleading here: `updated_at` NULL genuinely means "not edited since
this feature shipped," and there is no discreet "last edited" fact for
these rows to recover — `created_at` is a different, already-existing
concept (row creation) that this migration must not conflate with it.

Revision ID: 0021_product_updated_at
Revises: 0020_purchase_order_sent_at
"""
from alembic import op
import sqlalchemy as sa

revision = "0021_product_updated_at"
down_revision = "0020_purchase_order_sent_at"
branch_labels = None
depends_on = None


def _has_column(bind, table: str, column: str) -> bool:
    return column in {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind, "products", "updated_at"):
        op.add_column("products", sa.Column("updated_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, "products", "updated_at"):
        op.drop_column("products", "updated_at")
