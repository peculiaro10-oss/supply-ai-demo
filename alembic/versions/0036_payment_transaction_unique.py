"""Reject reuse of one provider transaction across payment records.

Fails on historical duplicate IDs for operator reconciliation; never deletes or
rewrites financial history to force a migration through.
"""
from alembic import op
import sqlalchemy as sa
revision = '0036_payment_transaction_unique'
down_revision = '0035_currency_normalization'
branch_labels = None
depends_on = None

def upgrade():
    count = op.get_bind().execute(sa.text('SELECT COUNT(*) FROM (SELECT paystack_transaction_id FROM payment_records WHERE paystack_transaction_id IS NOT NULL GROUP BY paystack_transaction_id HAVING COUNT(*) > 1) duplicates')).scalar()
    if count:
        raise RuntimeError('Duplicate provider transaction IDs require billing reconciliation before migration.')
    constraints = sa.inspect(op.get_bind()).get_unique_constraints('payment_records')
    if not any(set(item.get('column_names') or []) == {'paystack_transaction_id'} for item in constraints):
        op.create_unique_constraint('uq_payment_records_provider_transaction', 'payment_records', ['paystack_transaction_id'])

def downgrade():
    constraints = sa.inspect(op.get_bind()).get_unique_constraints('payment_records')
    if any(item.get('name') == 'uq_payment_records_provider_transaction' for item in constraints):
        op.drop_constraint('uq_payment_records_provider_transaction', 'payment_records', type_='unique')
