"""Baseline Cauldra schema.

Revision ID: 0001_baseline_schema
Revises: None
"""
from alembic import op

revision = "0001_baseline_schema"
down_revision = None
branch_labels = None
depends_on = None

def upgrade() -> None:
    # Metadata is the single current schema definition. checkfirst keeps this
    # baseline safe when a freshly imported database already has core tables.
    from main import Base
    Base.metadata.create_all(bind=op.get_bind(), checkfirst=True)

def downgrade() -> None:
    # The baseline represents all customer data. It is intentionally not
    # reversible through an automated destructive drop.
    raise RuntimeError("Refusing to drop the Cauldra baseline schema automatically.")
