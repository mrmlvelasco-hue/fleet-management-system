"""Stage 3B follow-up: add branches.company_id

Non-destructive. One Company per Branch (the EDL Group hierarchy a
Company has several Branches under it), stored once on Branch so any
branch-scoped master (Driver, Department, ...) gets Company for free via
branch_id, with no column or migration of its own.

Revision ID: c7f1a2b6e8d3
Revises: b2e6f3a9d4c1
Create Date: 2026-10-29
"""
from alembic import op
import sqlalchemy as sa

revision = "c7f1a2b6e8d3"
down_revision = "b2e6f3a9d4c1"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("branches") as batch_op:
        batch_op.add_column(
            sa.Column("company_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_branches_company_id", "org_companies",
            ["company_id"], ["id"])


def downgrade():
    with op.batch_alter_table("branches") as batch_op:
        batch_op.drop_constraint("fk_branches_company_id",
                                 type_="foreignkey")
        batch_op.drop_column("company_id")
