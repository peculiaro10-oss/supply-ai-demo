"""Sale transaction header — race-safe checkout idempotency.

Adds the sale_transactions table: one row per CHECKOUT, holding the header
for the one-or-more sales line rows that checkout created.

WHY THIS EXISTS (reproduced, not theoretical):
Checkout's duplicate guard was a plain read — "SELECT sales WHERE client_ref
= ?; if found, return early". That is a time-of-check/time-of-use race. Two
genuinely concurrent submissions of the same cart (a fast double-click
dispatching both requests before either commits) BOTH passed that check and
BOTH recorded a sale. Verified against a live server: two concurrent
identical checkouts of quantity 1 produced 2 sale rows and units_sold went
up by 2 — one unit sold, reported as two — while one request's stock
decrement was silently lost to the other's write.

A read can never close that race; only the database can. The
UNIQUE(business_id, client_ref) constraint on this table is the fix:
checkout inserts the header BEFORE touching stock, so exactly one of the
racing requests survives and the other returns the winner's result having
written nothing.

Reporting deliberately does NOT read this table — transaction counts still
go through COUNT(DISTINCT sales.client_ref), which also covers rows that
predate this table. So no historical figure changes as a result of this
migration.

Purely additive: one new table (created via Base.metadata.create_all with
checkfirst=True, the same pattern 0002/0007 used). No existing table,
column, or row is touched; no backfill is performed — pre-existing sales
simply have no header, which is correct, since their checkout was never
subject to this guard.

Revision ID: 0009_sale_transaction_header
Revises: 0008_sale_snapshots_and_indexes
"""
from alembic import op

revision = "0009_sale_transaction_header"
down_revision = "0008_sale_snapshots_and_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    from main import Base
    Base.metadata.create_all(bind=bind, checkfirst=True, tables=[Base.metadata.tables["sale_transactions"]])
    op.execute("CREATE INDEX IF NOT EXISTS ix_sale_transactions_business_day ON sale_transactions (business_id, business_day_id)")


def downgrade() -> None:
    # Dropping this table removes the only thing preventing duplicate
    # concurrent checkouts from double-recording sales. It holds real
    # financial records (one row per completed checkout), so refuse to drop
    # it automatically — matching the policy the baseline/integrity/refund
    # migrations already use for financial data in this app.
    raise RuntimeError("Refusing to drop sale_transactions (checkout headers / duplicate-sale guard) automatically.")
