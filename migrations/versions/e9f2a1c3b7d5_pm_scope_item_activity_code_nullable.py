"""Make PMScopeItem.activity_code nullable for save-as-template

Revision ID: e9f2a1c3b7d5
Revises: d7b3f05e81c4
Create Date: 2026-10-05 09:00:00.000000

PMScopeItem.activity_code was NOT NULL. save_scope_as_template() copies
activity rows from an order's checklist, and hand-entered lines may have
no code (activity_code = None). Without this change those saves fail at
flush with an IntegrityError.

No data loss: every existing row has a value (the column was always
populated before this); relaxing the constraint changes only what future
rows are allowed to omit.

Downgrade: any row with a NULL code gets '' before reverting to NOT NULL,
matching the old "ACT-N" fallback the template create API used for
missing codes.
"""
from alembic import op
import sqlalchemy as sa


revision = "e9f2a1c3b7d5"
down_revision = "d7b3f05e81c4"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("pm_scope_items", schema=None) as batch_op:
        batch_op.alter_column("activity_code",
                              existing_type=sa.String(length=40),
                              nullable=True)


def downgrade():
    items = sa.table("pm_scope_items",
                     sa.column("activity_code", sa.String(40)))
    op.execute(items.update()
               .where(items.c.activity_code.is_(None))
               .values(activity_code=""))
    with op.batch_alter_table("pm_scope_items", schema=None) as batch_op:
        batch_op.alter_column("activity_code",
                              existing_type=sa.String(length=40),
                              nullable=False)
