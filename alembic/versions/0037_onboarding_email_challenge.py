"""Fresh challenge-bound email proof. Existing authorizations cannot bypass it.

Idempotent (MIGR-001): 0001_baseline_schema builds the CURRENT model set with
create_all(), so on an empty database every object below already exists by the
time this runs. Each step checks for its own prior existence first, exactly as
0024_locations does, so a fresh provision and an upgraded database converge.
"""
from alembic import op
import sqlalchemy as sa
revision = '0037_onboarding_email_challenge'
down_revision = '0036_payment_transaction_unique'
branch_labels = None
depends_on = None


def _has_table(bind, table: str) -> bool:
    return table in sa.inspect(bind).get_table_names()


def _has_column(bind, table: str, column: str) -> bool:
    return column in {c["name"] for c in sa.inspect(bind).get_columns(table)}


def _has_index(bind, table: str, index: str) -> bool:
    return index in {i["name"] for i in sa.inspect(bind).get_indexes(table)}


def _has_fk(bind, table: str, columns: list, referred_table: str) -> bool:
    return any(
        fk.get("referred_table") == referred_table and list(fk.get("constrained_columns") or []) == columns
        for fk in sa.inspect(bind).get_foreign_keys(table)
    )


def _has_unique(bind, table: str, columns: list) -> bool:
    inspector = sa.inspect(bind)
    if any(list(u.get("column_names") or []) == columns for u in inspector.get_unique_constraints(table)):
        return True
    return any(i.get("unique") and list(i.get("column_names") or []) == columns for i in inspector.get_indexes(table))


def upgrade():
    bind = op.get_bind()
    if not _has_table(bind, 'onboarding_email_challenges'):
        op.create_table('onboarding_email_challenges',
            sa.Column('challenge_id', sa.String(64), primary_key=True),
            *[sa.Column(n, sa.String(), nullable=False) for n in ('email','plan','billing_interval','platform','return_target','status')],
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('expires_at', sa.DateTime(), nullable=False),
            sa.Column('verified_at', sa.DateTime()), sa.Column('consumed_at', sa.DateTime()))
    if not _has_index(bind, 'onboarding_email_challenges', 'ix_onboarding_email_challenges_expires_at'):
        op.create_index('ix_onboarding_email_challenges_expires_at','onboarding_email_challenges',['expires_at'])
    if not _has_column(bind, 'onboarding_authorizations', 'email_challenge_id'):
        op.add_column('onboarding_authorizations',sa.Column('email_challenge_id',sa.String(64),nullable=True))
    if not _has_fk(bind, 'onboarding_authorizations', ['email_challenge_id'], 'onboarding_email_challenges'):
        op.create_foreign_key('fk_onboarding_email_challenge','onboarding_authorizations','onboarding_email_challenges',['email_challenge_id'],['challenge_id'])
    if not _has_unique(bind, 'onboarding_authorizations', ['email_challenge_id']):
        op.create_unique_constraint('uq_onboarding_email_challenge','onboarding_authorizations',['email_challenge_id'])
def downgrade():
    op.drop_constraint('uq_onboarding_email_challenge','onboarding_authorizations',type_='unique')
    op.drop_constraint('fk_onboarding_email_challenge','onboarding_authorizations',type_='foreignkey')
    op.drop_column('onboarding_authorizations','email_challenge_id')
    op.drop_table('onboarding_email_challenges')
