"""Persist truthful Paystack verification-charge refund state.

Revision ID: 0015_paystack_refund_state
Revises: 0014_unknown_historical_cogs
"""
from alembic import op
import sqlalchemy as sa


revision = "0015_paystack_refund_state"
down_revision = "0014_unknown_historical_cogs"
branch_labels = None
depends_on = None


REFUND_COLUMNS = (
    sa.Column("refund_status", sa.String(), nullable=False, server_default="not_requested"),
    sa.Column("refund_provider_status", sa.String(), nullable=True),
    sa.Column("refund_provider_id", sa.String(), nullable=True),
    sa.Column("refund_attempt_count", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("refund_requested_at", sa.DateTime(), nullable=True),
    sa.Column("refund_updated_at", sa.DateTime(), nullable=True),
    sa.Column("refund_last_error", sa.Text(), nullable=True),
    sa.Column("paystack_transaction_id", sa.String(), nullable=True),
)


def _add_columns(table: str) -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {column["name"] for column in inspector.get_columns(table)}
    for template in REFUND_COLUMNS:
        if template.name not in existing_columns:
            op.add_column(table, sa.Column(
                template.name, template.type,
                nullable=template.nullable,
                server_default=template.server_default,
            ))
    op.alter_column(table, "refund_status", server_default="not_requested")
    op.alter_column(table, "refund_attempt_count", server_default="0")
    inspector = sa.inspect(bind)
    existing_indexes = {index["name"] for index in inspector.get_indexes(table)}
    if f"ix_{table}_refund_status" not in existing_indexes:
        op.create_index(f"ix_{table}_refund_status", table, ["refund_status"])
    if f"ix_{table}_refund_provider_id" not in existing_indexes:
        op.create_index(f"ix_{table}_refund_provider_id", table, ["refund_provider_id"])
    existing_checks = {constraint["name"] for constraint in inspector.get_check_constraints(table)}
    if f"ck_{table}_refund_status" not in existing_checks:
        op.create_check_constraint(
            f"ck_{table}_refund_status",
            table,
            "refund_status IN ('not_requested', 'pending', 'succeeded', 'failed')",
        )


def upgrade() -> None:
    _add_columns("payment_records")
    _add_columns("onboarding_authorizations")

    # Historical refunded_at was written unconditionally even when the provider
    # call failed, so it is explicitly NOT evidence of success. Keep those rows
    # pending reconciliation and clear the dishonest completion timestamp.
    op.execute("UPDATE payment_records SET refund_status = 'pending', refund_provider_status = 'legacy_unverified', refunded_at = NULL WHERE refunded_at IS NOT NULL")
    op.execute("UPDATE onboarding_authorizations SET refund_status = 'pending', refund_provider_status = 'legacy_unverified', refunded_at = NULL WHERE refunded_at IS NOT NULL")


def downgrade() -> None:
    raise RuntimeError("Refusing to discard durable Paystack refund state automatically.")
