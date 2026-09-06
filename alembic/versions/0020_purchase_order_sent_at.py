"""Purchase Order sent_at timestamp, with a safe backfill for existing SENT rows.

Adds one new nullable column, `purchase_orders.sent_at`. No existing column,
table, or row is dropped, renamed, or destroyed.

Why this column exists: subscription usage for Purchase Orders must be based
on when an order was actually SENT, not when it was generated as a draft (a
draft can be generated in one billing period and sent in a later one, and
must never itself consume the plan's allowance — see check_plan_limit() calls
in main.py's dispatch_po_email() / confirm_po_whatsapp_sent(), and their
removal from generate_po()). That requires a real "sent" timestamp distinct
from `created_at`, which has always meant "when this row was first generated
as a draft" and must keep meaning exactly that.

Backfill decision for rows that already have status="SENT" from before this
column existed (documented here per the task's explicit request):

    UPDATE purchase_orders SET sent_at = created_at
    WHERE status = 'SENT' AND sent_at IS NULL

`created_at` is the ONLY timestamp that has ever existed on this model (see
main.py's PurchaseOrder class: id, supplier_id, status, total_estimated_cost,
email_draft, business_id, owner_id, created_at — nothing else), so it is the
safest available fallback: it is a real, already-recorded moment for that
specific row, never NULL, and never in the future relative to when the order
could possibly have been sent. It is an approximation — a PO generated as a
draft and sent hours or days later will show its ORIGINAL generation time as
its sent_at, not its true send time — and for any such row that approximation
could shift which historical billing period it appears to have been sent in.
This backfill runs exactly once, only for rows that already say SENT and
still have no sent_at (idempotent: a re-run of this migration touches zero
rows the second time). Every purchase order sent AFTER this migration gets a
real, accurate sent_at at the moment dispatch_po_email()/
confirm_po_whatsapp_sent() actually marks it SENT — this approximation only
ever affects pre-migration historical rows, never new ones. No row is deleted
or hidden; existing Purchase Order history is fully preserved either way.

Revision ID: 0020_purchase_order_sent_at
Revises: 0019_general_catalog_identity_hardening
"""
from alembic import op
import sqlalchemy as sa

revision = "0020_purchase_order_sent_at"
down_revision = "0019_general_catalog_identity_hardening"
branch_labels = None
depends_on = None


def _has_column(bind, table: str, column: str) -> bool:
    return column in {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind, "purchase_orders", "sent_at"):
        op.add_column("purchase_orders", sa.Column("sent_at", sa.DateTime(), nullable=True))

    # Backfill: existing SENT rows with no sent_at get created_at as the
    # safest available fallback (see module docstring above for the full
    # reasoning). Scoped to status='SENT' AND sent_at IS NULL, so this is
    # idempotent and never touches a DRAFT or an already-backfilled row.
    op.execute(
        "UPDATE purchase_orders SET sent_at = created_at "
        "WHERE status = 'SENT' AND sent_at IS NULL"
    )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, "purchase_orders", "sent_at"):
        op.drop_column("purchase_orders", "sent_at")
