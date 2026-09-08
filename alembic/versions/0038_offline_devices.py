"""Device-bound offline authorization; replay receipts use mutation_idempotency."""
from alembic import op
import sqlalchemy as sa
revision = '0038_offline_devices'
down_revision = '0037_onboarding_email_challenge'
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('offline_devices',
        sa.Column('device_id', sa.String(36), primary_key=True),
        sa.Column('business_id', sa.Integer(), sa.ForeignKey('business_profile.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('auth_version', sa.Integer(), nullable=False),
        sa.Column('role', sa.String(40), nullable=False),
        sa.Column('permissions_hash', sa.String(64), nullable=False),
        sa.Column('issued_at', sa.DateTime(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('revoked_at', sa.DateTime()))
    op.create_index('ix_offline_devices_business_id', 'offline_devices', ['business_id'])
    op.create_index('ix_offline_devices_user_id', 'offline_devices', ['user_id'])

def downgrade():
    op.drop_table('offline_devices')
