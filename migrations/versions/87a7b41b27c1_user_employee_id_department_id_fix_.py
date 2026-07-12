"""user employee_id/department_id, fix branch_id FK

Revision ID: 87a7b41b27c1
Revises: a4d54ba9d40a
Create Date: 2026-07-12 11:53:05.344858

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '87a7b41b27c1'
down_revision = 'a4d54ba9d40a'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('employee_id', sa.String(length=40), nullable=True))
        batch_op.add_column(sa.Column('department_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_users_department_id',
                                    'departments', ['department_id'], ['id'])
        batch_op.create_foreign_key('fk_users_branch_id',
                                    'branches', ['branch_id'], ['id'])


def downgrade():
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_constraint('fk_users_branch_id', type_='foreignkey')
        batch_op.drop_constraint('fk_users_department_id', type_='foreignkey')
        batch_op.drop_column('department_id')
        batch_op.drop_column('employee_id')
