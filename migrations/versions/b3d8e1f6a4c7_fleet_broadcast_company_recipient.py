"""Batch 8: Fleet Broadcast audience by Company

Michael's instruction (2026-10-31): "the broadcast is also possible to
select per company" -- alongside the existing ALL_USERS/ROLE/
SPECIFIC_USER/BRANCH/DEPARTMENT audiences, not instead of them.

Adds `company_id` to fleet_broadcast_recipients, mirroring the
existing `branch_id`/`department_id` columns on the same table: a
plain, unconstrained, indexed Integer rather than a ForeignKey, for
the same reason those two are -- a recipient row records which
Company the audience was DEFINED against at the time, and should not
be forced to vanish if that Company is later deleted.

NON-DESTRUCTIVE AND REVERSIBLE. One new, nullable column; no existing
column or table is touched.

Revision ID: b3d8e1f6a4c7
Revises: a1c4e7f93d82
Create Date: 2026-10-31
"""
from alembic import op
import sqlalchemy as sa

revision = "b3d8e1f6a4c7"
down_revision = "a1c4e7f93d82"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table(
            "fleet_broadcast_recipients", schema=None) as batch_op:
        batch_op.add_column(sa.Column("company_id", sa.Integer(),
                                      nullable=True))
        batch_op.create_index(
            batch_op.f("ix_fleet_broadcast_recipients_company_id"),
            ["company_id"], unique=False)


def downgrade():
    with op.batch_alter_table(
            "fleet_broadcast_recipients", schema=None) as batch_op:
        batch_op.drop_index(
            batch_op.f("ix_fleet_broadcast_recipients_company_id"))
        batch_op.drop_column("company_id")
