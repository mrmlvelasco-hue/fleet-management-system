"""Stage 3D: company_id on every branch-scoped table

Michael's instruction (2026-10-29): Company replaces Branch across the
whole system, mobile included.

Until now Company lived on four tables (branches, vehicles,
user_org_scopes, approval_instances) and everything else resolved it
through branch_id. That was correct while Branch was staying; once
Branch is being retired, inheriting from a column that is going away is
not a foundation, so each branch-scoped table gets its own company_id.

NON-DESTRUCTIVE AND REVERSIBLE. Every column added here is nullable, no
existing column is altered, no data is written, and nothing reads these
columns yet -- Branch keeps working exactly as before. Populating them
is a separate, re-runnable step (CompanyBackfillService), and switching
the code to read them is a later one again, gated on reconciliation.

Deliberately NOT done here, because each needs its own decision:
  * departments uniqueness is still (code, branch_id); re-keying it to
    (code, company_id) can collide in live data;
  * vehicles.branch_id / drivers.branch_id / departments.branch_id stay
    NOT NULL;
  * users.branch_id remains the data-access boundary -- users.company_id
    grants nothing until the boundary is explicitly moved.

Revision ID: d4a7c91f2b60
Revises: c7f1a2b6e8d3
Create Date: 2026-10-29
"""
from alembic import op
import sqlalchemy as sa

revision = "d4a7c91f2b60"
down_revision = "c7f1a2b6e8d3"
branch_labels = None
depends_on = None


# (table, column) for every single-column addition. MaintenanceOrder
# takes two (the transfer's From and To ends) and vendors take an
# association table, both handled separately below.
_COLUMNS = [
    ("drivers", "company_id"),
    ("departments", "company_id"),
    ("tires", "company_id"),
    ("batteries", "company_id"),
    ("users", "company_id"),
    ("vehicle_handover_checklists", "company_id"),
    ("vehicle_checklists", "company_id"),
    ("approval_tasks", "company_id"),
    ("maintenance_orders", "origin_company_id"),
    ("maintenance_orders", "destination_company_id"),
]


def upgrade():
    for table, column in _COLUMNS:
        with op.batch_alter_table(table) as batch_op:
            batch_op.add_column(
                sa.Column(column, sa.Integer(), nullable=True))
            batch_op.create_foreign_key(
                f"fk_{table}_{column}", "org_companies", [column], ["id"])
            batch_op.create_index(f"ix_{table}_{column}", [column])

    # Vendors serve MANY companies, mirroring vendor_branches.
    op.create_table(
        "vendor_companies",
        sa.Column("vendor_id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["vendor_id"], ["vendors.id"]),
        sa.ForeignKeyConstraint(["company_id"], ["org_companies.id"]),
        sa.PrimaryKeyConstraint("vendor_id", "company_id"),
    )


def downgrade():
    op.drop_table("vendor_companies")
    for table, column in reversed(_COLUMNS):
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_index(f"ix_{table}_{column}")
            batch_op.drop_constraint(f"fk_{table}_{column}",
                                     type_="foreignkey")
            batch_op.drop_column(column)
