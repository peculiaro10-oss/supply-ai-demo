"""Atomic idempotency claims for offline-capable mutations.

Revision ID: 0011_mutation_idempotency
Revises: 0010_notifications
"""
from alembic import op
import sqlalchemy as sa

revision = "0011_mutation_idempotency"
down_revision = "0010_notifications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table("mutation_idempotency"):
        return
    op.create_table(
        "mutation_idempotency",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("business_id", sa.Integer(), nullable=False),
        sa.Column("operation", sa.String(), nullable=False),
        sa.Column("client_ref", sa.String(), nullable=False),
        sa.Column("request_hash", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("response_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["business_id"], ["business_profile.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "business_id", "operation", "client_ref",
            name="uq_mutation_idempotency_business_operation_ref",
        ),
    )
    op.create_index("ix_mutation_idempotency_id", "mutation_idempotency", ["id"], unique=False)
    op.create_index("ix_mutation_idempotency_business_id", "mutation_idempotency", ["business_id"], unique=False)
    op.create_index("ix_mutation_idempotency_status", "mutation_idempotency", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_mutation_idempotency_status", table_name="mutation_idempotency")
    op.drop_index("ix_mutation_idempotency_business_id", table_name="mutation_idempotency")
    op.drop_index("ix_mutation_idempotency_id", table_name="mutation_idempotency")
    op.drop_table("mutation_idempotency")
