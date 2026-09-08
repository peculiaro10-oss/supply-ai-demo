"""Fresh challenge-bound email proof. Existing authorizations cannot bypass it."""
from alembic import op
import sqlalchemy as sa
revision = '0037_onboarding_email_challenge'
down_revision = '0036_payment_transaction_unique'
branch_labels = None
depends_on = None
def upgrade():
    op.create_table('onboarding_email_challenges',
        sa.Column('challenge_id', sa.String(64), primary_key=True),
        *[sa.Column(n, sa.String(), nullable=False) for n in ('email','plan','billing_interval','platform','return_target','status')],
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('verified_at', sa.DateTime()), sa.Column('consumed_at', sa.DateTime()))
    op.create_index('ix_onboarding_email_challenges_expires_at','onboarding_email_challenges',['expires_at'])
    op.add_column('onboarding_authorizations',sa.Column('email_challenge_id',sa.String(64),nullable=True))
    op.create_foreign_key('fk_onboarding_email_challenge','onboarding_authorizations','onboarding_email_challenges',['email_challenge_id'],['challenge_id'])
    op.create_unique_constraint('uq_onboarding_email_challenge','onboarding_authorizations',['email_challenge_id'])
def downgrade():
    op.drop_constraint('uq_onboarding_email_challenge','onboarding_authorizations',type_='unique')
    op.drop_constraint('fk_onboarding_email_challenge','onboarding_authorizations',type_='foreignkey')
    op.drop_column('onboarding_authorizations','email_challenge_id')
    op.drop_table('onboarding_email_challenges')
