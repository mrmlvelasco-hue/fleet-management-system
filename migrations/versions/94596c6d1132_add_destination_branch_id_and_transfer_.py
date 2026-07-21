"""add destination_branch_id and transfer_reference_number to maintenance_orders

Revision ID: 94596c6d1132
Revises: 4447eb463114
Create Date: 2026-07-21 04:53:15.085989

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '94596c6d1132'
down_revision = '4447eb463114'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('maintenance_orders', schema=None) as batch_op:
        batch_op.add_column(sa.Column('destination_branch_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('transfer_reference_number', sa.String(length=40), nullable=True))
        batch_op.create_foreign_key(
            'fk_maintenance_orders_destination_branch_id_branches',
            'branches', ['destination_branch_id'], ['id'])


def downgrade():
    with op.batch_alter_table('maintenance_orders', schema=None) as batch_op:
        batch_op.drop_constraint(
            'fk_maintenance_orders_destination_branch_id_branches',
            type_='foreignkey')
        batch_op.drop_column('transfer_reference_number')
        batch_op.drop_column('destination_branch_id')
