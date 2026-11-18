"""Custom reports: AND/OR filter logic and group-by

Revision ID: b8d4f2a6c915
Revises: a7c3e9f1d204
Create Date: 2026-11-18 00:00:00.000000

Two nullable-safe columns on custom_reports. Every existing report keeps
working unchanged: filter_logic defaults to AND (what filters always
meant) and group_by to NULL (no grouping). Nothing else is altered.
"""
from alembic import op
import sqlalchemy as sa

revision = 'b8d4f2a6c915'
down_revision = 'a7c3e9f1d204'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('custom_reports', schema=None) as batch_op:
        batch_op.add_column(sa.Column('filter_logic', sa.String(length=3),
                                      nullable=False, server_default='AND'))
        batch_op.add_column(sa.Column('group_by', sa.String(length=60),
                                      nullable=True))


def downgrade():
    with op.batch_alter_table('custom_reports', schema=None) as batch_op:
        batch_op.drop_column('group_by')
        batch_op.drop_column('filter_logic')
