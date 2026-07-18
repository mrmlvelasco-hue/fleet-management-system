"""MO Category/Transaction Type - new mo_transaction_types table, order_category/transaction_type_id on maintenance_orders, maintenance_type_id/category now nullable

Revision ID: ec99571c95ef
Revises: f274c69d7b39
Create Date: 2026-07-18 17:34:15.426284

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'ec99571c95ef'
down_revision = 'f274c69d7b39'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('mo_transaction_types',
    sa.Column('code', sa.String(length=30), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('order_category', sa.String(length=15), nullable=False),
    sa.Column('group', sa.String(length=20), nullable=True),
    sa.Column('sort_order', sa.Integer(), nullable=False),
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.Column('created_by', sa.Integer(), nullable=True),
    sa.Column('updated_by', sa.Integer(), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('mo_transaction_types', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_mo_transaction_types_code'), ['code'], unique=True)

    with op.batch_alter_table('maintenance_orders', schema=None) as batch_op:
        # server_default required: MySQL rejects adding a NOT NULL column
        # to a table that already has rows unless a default is given to
        # backfill them. Every existing order becomes order_category=
        # MAINTENANCE, matching the Python-side default and preserving
        # every existing PM/CM order's behavior exactly.
        batch_op.add_column(sa.Column('order_category', sa.String(length=15),
                                      nullable=False, server_default='MAINTENANCE'))
        batch_op.add_column(sa.Column('transaction_type_id', sa.Integer(), nullable=True))
        batch_op.alter_column('maintenance_type_id',
               existing_type=sa.INTEGER(),
               nullable=True)
        batch_op.alter_column('category',
               existing_type=sa.VARCHAR(length=12),
               nullable=True)
        batch_op.create_foreign_key('fk_maintenance_orders_transaction_type_id',
                                    'mo_transaction_types', ['transaction_type_id'], ['id'])
    with op.batch_alter_table('maintenance_orders', schema=None) as batch_op:
        batch_op.alter_column('order_category', server_default=None)


def downgrade():
    with op.batch_alter_table('maintenance_orders', schema=None) as batch_op:
        batch_op.drop_constraint('fk_maintenance_orders_transaction_type_id', type_='foreignkey')
        batch_op.alter_column('category',
               existing_type=sa.VARCHAR(length=12),
               nullable=False)
        batch_op.alter_column('maintenance_type_id',
               existing_type=sa.INTEGER(),
               nullable=False)
        batch_op.drop_column('transaction_type_id')
        batch_op.drop_column('order_category')

    with op.batch_alter_table('mo_transaction_types', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_mo_transaction_types_code'))

    op.drop_table('mo_transaction_types')
