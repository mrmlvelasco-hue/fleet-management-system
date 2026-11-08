"""Stage 3D follow-up: departments.branch_id becomes nullable, Company
replaces Branch as the department's organisational owner

Michael's decision (2026-11-08), after finding the Department form
still showed Branch: convert it the same way Vehicle and Driver were
converted on 2026-10-30 -- remove Branch from the form, require
Company in its place.

Verified safe first, not assumed: Michael ran
check_department_company_collision.py against his real data (4
departments, 0 unmapped via effective_company_id(), 0 (code, effective
company) collisions) before this migration was written. That script
checks exactly what this migration's uniqueness change depends on.

SAFE IN BOTH DIRECTIONS FOR EXISTING DATA: relaxing NOT NULL never
rejects a row, and dropping/adding a unique constraint touches no
row's data, only what future inserts are checked against.

The DOWNGRADE is deliberately strict, same shape as
e5b3d81c7a24_branch_nullable_on_vehicle_and_driver: restoring the old
(code, branch_id) constraint and NOT NULL cannot succeed while any row
holds a null branch_id, so it refuses with a clear message rather than
guessing a branch for a department that was created without one.

Revision ID: a8e2f615c9d3
Revises: f3a7c9e1b4d2
Create Date: 2026-11-08
"""
from alembic import op
import sqlalchemy as sa

revision = "a8e2f615c9d3"
down_revision = "f3a7c9e1b4d2"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("departments", schema=None) as batch_op:
        batch_op.alter_column("branch_id", existing_type=sa.Integer(),
                              nullable=True)
        batch_op.drop_constraint("uq_dept_code_branch", type_="unique")
        batch_op.create_unique_constraint(
            "uq_dept_code_company", ["code", "company_id"])


def downgrade():
    conn = op.get_bind()
    orphans = conn.execute(sa.text(
        "SELECT COUNT(*) FROM departments WHERE branch_id IS NULL"
    )).scalar() or 0
    if orphans:
        raise RuntimeError(
            f"Cannot restore NOT NULL on departments.branch_id: {orphans} "
            f"row(s) have no branch. Assign a branch to those records "
            f"first -- this migration will not guess one for them."
        )
    with op.batch_alter_table("departments", schema=None) as batch_op:
        batch_op.drop_constraint("uq_dept_code_company", type_="unique")
        batch_op.create_unique_constraint(
            "uq_dept_code_branch", ["code", "branch_id"])
        batch_op.alter_column("branch_id", existing_type=sa.Integer(),
                              nullable=False)
