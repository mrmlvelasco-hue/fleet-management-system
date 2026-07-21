"""add origin_branch_id to maintenance_orders for asset transfer report

Revision ID: 733c1e1ea050
Revises: 94596c6d1132
Create Date: 2026-07-21 04:56:11.091434

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '733c1e1ea050'
down_revision = '94596c6d1132'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('maintenance_orders', schema=None) as batch_op:
        batch_op.add_column(sa.Column('origin_branch_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            'fk_maintenance_orders_origin_branch_id_branches',
            'branches', ['origin_branch_id'], ['id'])


def downgrade():
    with op.batch_alter_table('maintenance_orders', schema=None) as batch_op:
        batch_op.drop_constraint(
            'fk_maintenance_orders_origin_branch_id_branches',
            type_='foreignkey')
        batch_op.drop_column('origin_branch_id')
