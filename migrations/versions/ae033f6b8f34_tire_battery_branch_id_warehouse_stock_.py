"""tire/battery branch_id (warehouse/stock location) for org-scope viewing

Revision ID: ae033f6b8f34
Revises: 87a7b41b27c1
Create Date: 2026-07-12 14:41:13.517638

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'ae033f6b8f34'
down_revision = '87a7b41b27c1'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('batteries', schema=None) as batch_op:
        batch_op.add_column(sa.Column('branch_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_batteries_branch_id',
                                    'branches', ['branch_id'], ['id'])

    with op.batch_alter_table('tires', schema=None) as batch_op:
        batch_op.add_column(sa.Column('branch_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_tires_branch_id',
                                    'branches', ['branch_id'], ['id'])


def downgrade():
    with op.batch_alter_table('tires', schema=None) as batch_op:
        batch_op.drop_constraint('fk_tires_branch_id', type_='foreignkey')
        batch_op.drop_column('branch_id')

    with op.batch_alter_table('batteries', schema=None) as batch_op:
        batch_op.drop_constraint('fk_batteries_branch_id', type_='foreignkey')
        batch_op.drop_column('branch_id')
