"""Stage 3D: vehicles.branch_id and drivers.branch_id become nullable

Michael's decision (2026-10-30): remove Branch from the Vehicle and
Driver forms and make Company the required field in its place. Those
columns were NOT NULL, so the form change is impossible without this.

SAFE IN BOTH DIRECTIONS FOR EXISTING DATA: relaxing NOT NULL never
rejects a row, and every existing record keeps the branch it already
has. Only records created AFTER this may have a null branch.

The non-obvious consequence, handled in the application rather than
here: UserOrgScopeService.covers() short-circuits to True when no org
context is passed, so a record with branch_id = NULL would be visible to
EVERY user regardless of scope. VehicleService now passes the effective
company_id alongside branch_id, and list_page() honours the ORG_COMPANY
/ ORG_REGION / ORG_AREA_DISTRICT scopes in SQL, so a null branch
resolves through Company instead of falling through. Requiring Company
on the form is what guarantees there is always something to resolve.

The DOWNGRADE is deliberately strict: restoring NOT NULL cannot succeed
while any row holds a null branch, so it refuses with a clear message
rather than silently inventing a branch for records that never had one.

Revision ID: e5b3d81c7a24
Revises: d4a7c91f2b60
Create Date: 2026-10-30
"""
from alembic import op
import sqlalchemy as sa

revision = "e5b3d81c7a24"
down_revision = "d4a7c91f2b60"
branch_labels = None
depends_on = None

_TABLES = ("vehicles", "drivers")


def upgrade():
    for table in _TABLES:
        with op.batch_alter_table(table) as batch_op:
            batch_op.alter_column("branch_id", existing_type=sa.Integer(),
                                  nullable=True)


def downgrade():
    conn = op.get_bind()
    for table in _TABLES:
        orphans = conn.execute(sa.text(
            f"SELECT COUNT(*) FROM {table} WHERE branch_id IS NULL"
        )).scalar() or 0
        if orphans:
            raise RuntimeError(
                f"Cannot restore NOT NULL on {table}.branch_id: {orphans} "
                f"row(s) have no branch. Assign a branch to those records "
                f"first -- this migration will not guess one for them."
            )
    for table in _TABLES:
        with op.batch_alter_table(table) as batch_op:
            batch_op.alter_column("branch_id", existing_type=sa.Integer(),
                                  nullable=False)
