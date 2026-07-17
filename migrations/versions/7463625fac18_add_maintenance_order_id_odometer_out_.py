"""add maintenance_order_id, odometer_out, odometer_in to authority_to_drives

Revision ID: 7463625fac18
Revises: 144d3c4c0cb0
Create Date: 2026-07-17 09:16:01.166282

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '7463625fac18'
down_revision = '144d3c4c0cb0'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('authority_to_drives', schema=None) as batch_op:
        batch_op.add_column(sa.Column('maintenance_order_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('odometer_out', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('odometer_in', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_authority_to_drives_maintenance_order_id',
                                    'maintenance_orders', ['maintenance_order_id'], ['id'])


def downgrade():
    with op.batch_alter_table('authority_to_drives', schema=None) as batch_op:
        batch_op.drop_constraint('fk_authority_to_drives_maintenance_order_id',
                                 type_='foreignkey')
        batch_op.drop_column('odometer_in')
        batch_op.drop_column('odometer_out')
        batch_op.drop_column('maintenance_order_id')
