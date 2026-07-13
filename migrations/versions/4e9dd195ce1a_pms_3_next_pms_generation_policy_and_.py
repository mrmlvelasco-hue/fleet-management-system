"""PMS-3: next PMS generation policy and next due calculation method

Revision ID: 4e9dd195ce1a
Revises: b1ec51b8847e
Create Date: 2026-07-13 22:33:07.969597

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '4e9dd195ce1a'
down_revision = 'b1ec51b8847e'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('pm_schedules', schema=None) as batch_op:
        batch_op.add_column(sa.Column(
            'next_pms_generation', sa.String(length=15), nullable=False,
            server_default='AUTO_SCHEDULE'))
        batch_op.add_column(sa.Column(
            'next_due_calculation_method', sa.String(length=20), nullable=False,
            server_default='ACTUAL_COMPLETION'))
    # Drop the server-side default after backfilling existing rows — the
    # Python-level default on the model still applies for new inserts;
    # we don't want a lingering DB-level default diverging from it.
    with op.batch_alter_table('pm_schedules', schema=None) as batch_op:
        batch_op.alter_column('next_pms_generation', server_default=None)
        batch_op.alter_column('next_due_calculation_method', server_default=None)


def downgrade():
    with op.batch_alter_table('pm_schedules', schema=None) as batch_op:
        batch_op.drop_column('next_due_calculation_method')
        batch_op.drop_column('next_pms_generation')
