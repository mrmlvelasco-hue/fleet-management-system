"""add driver_id to maintenance_orders for vehicle assignment memo

Revision ID: ec220707401d
Revises: 5f9509d3931b
Create Date: 2026-07-19 10:33:41.127157

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'ec220707401d'
down_revision = '5f9509d3931b'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('maintenance_orders', schema=None) as batch_op:
        batch_op.add_column(sa.Column('driver_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            'fk_maintenance_orders_driver_id_drivers',
            'drivers', ['driver_id'], ['id'])


def downgrade():
    with op.batch_alter_table('maintenance_orders', schema=None) as batch_op:
        batch_op.drop_constraint(
            'fk_maintenance_orders_driver_id_drivers', type_='foreignkey')
        batch_op.drop_column('driver_id')
