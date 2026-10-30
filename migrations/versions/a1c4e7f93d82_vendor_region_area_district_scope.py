"""Batch 7: Vendor scoping by Company, Region, and Area/District

Michael's instruction (2026-10-31): Vendor coverage must be
configurable per Company, Region, and Area/District -- not only per
Branch/Business Unit as today.

vendor_companies already exists (Stage 3D, d4a7c91f2b60) but was never
wired up: no ORM relationship, no service logic, no API, no form field.
This migration adds the two tables Company-level scoping alone cannot
cover -- a vendor assigned directly to "all of Luzon" (a Region) or to
one Area/District serves every Company under it, not one Company at a
time, the same many-to-many shape as vendor_branches/vendor_companies
for exactly the same reason: a vendor can legitimately serve several
Regions/Area-Districts.

NON-DESTRUCTIVE AND REVERSIBLE. Two new, empty, additive tables; no
existing table or column is touched.

Revision ID: a1c4e7f93d82
Revises: e5b3d81c7a24
Create Date: 2026-10-31
"""
from alembic import op
import sqlalchemy as sa

revision = "a1c4e7f93d82"
down_revision = "e5b3d81c7a24"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "vendor_regions",
        sa.Column("vendor_id", sa.Integer(), nullable=False),
        sa.Column("region_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["vendor_id"], ["vendors.id"]),
        sa.ForeignKeyConstraint(["region_id"], ["regions.id"]),
        sa.PrimaryKeyConstraint("vendor_id", "region_id"),
    )
    op.create_table(
        "vendor_area_districts",
        sa.Column("vendor_id", sa.Integer(), nullable=False),
        sa.Column("area_district_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["vendor_id"], ["vendors.id"]),
        sa.ForeignKeyConstraint(["area_district_id"], ["areas_districts.id"]),
        sa.PrimaryKeyConstraint("vendor_id", "area_district_id"),
    )


def downgrade():
    op.drop_table("vendor_area_districts")
    op.drop_table("vendor_regions")
