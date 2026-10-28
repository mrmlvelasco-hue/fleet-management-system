"""add Region, AreaDistrict, OrgCompany master tables and vehicles.company_id

Stage 3A of the Fuel + Org master prompt (see claude/
fuel-org-stage1-impact-analysis.md for the full design). Non-destructive:
new tables only, plus one new nullable FK column on vehicles -- nothing
existing is altered or backfilled here. Backfilling vehicles.company_id
from branch_id is Stage 3B's migration tool, driven by a client-approved
Branch -> Company mapping sheet, not guessed here.

Revision ID: 084f385f4149
Revises: e8b4f0d2c6a3
Create Date: 2026-10-28 06:30:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "084f385f4149"
down_revision = "e8b4f0d2c6a3"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    insp = sa.inspect(conn)
    existing = set(insp.get_table_names())

    if "regions" not in existing:
        op.create_table(
            "regions",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.Column("created_by", sa.Integer(), nullable=True),
            sa.Column("updated_by", sa.Integer(), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False),
            sa.Column("code", sa.String(length=20), nullable=False),
            sa.Column("name", sa.String(length=120), nullable=False),
            sa.Column("description", sa.String(length=255), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("code", name="uq_regions_code"),
        )
        op.create_index("ix_regions_code", "regions", ["code"])

    if "areas_districts" not in existing:
        op.create_table(
            "areas_districts",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.Column("created_by", sa.Integer(), nullable=True),
            sa.Column("updated_by", sa.Integer(), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False),
            sa.Column("code", sa.String(length=20), nullable=False),
            sa.Column("name", sa.String(length=120), nullable=False),
            sa.Column("region_id", sa.Integer(), nullable=False),
            sa.Column("description", sa.String(length=255), nullable=True),
            sa.ForeignKeyConstraint(["region_id"], ["regions.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("code", "region_id",
                                name="uq_area_district_code_region"),
        )
        op.create_index("ix_areas_districts_code", "areas_districts", ["code"])
        op.create_index("ix_areas_districts_region_id", "areas_districts",
                        ["region_id"])

    if "org_companies" not in existing:
        op.create_table(
            "org_companies",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.Column("created_by", sa.Integer(), nullable=True),
            sa.Column("updated_by", sa.Integer(), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False),
            sa.Column("short_name", sa.String(length=80), nullable=False),
            sa.Column("company_name", sa.String(length=200), nullable=True),
            sa.Column("region_id", sa.Integer(), nullable=False),
            sa.Column("area_district_id", sa.Integer(), nullable=False),
            sa.Column("legacy_branch_id", sa.Integer(), nullable=True),
            sa.ForeignKeyConstraint(["region_id"], ["regions.id"]),
            sa.ForeignKeyConstraint(["area_district_id"],
                                    ["areas_districts.id"]),
            sa.ForeignKeyConstraint(["legacy_branch_id"], ["branches.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("short_name", "region_id", "area_district_id",
                                name="uq_company_shortname_region_area"),
        )
        op.create_index("ix_org_companies_short_name", "org_companies",
                        ["short_name"])

    vehicle_cols = {c["name"] for c in insp.get_columns("vehicles")}
    if "company_id" not in vehicle_cols:
        with op.batch_alter_table("vehicles") as batch_op:
            batch_op.add_column(
                sa.Column("company_id", sa.Integer(), nullable=True))
            batch_op.create_foreign_key(
                "fk_vehicles_company_id", "org_companies",
                ["company_id"], ["id"])


def downgrade():
    with op.batch_alter_table("vehicles") as batch_op:
        batch_op.drop_constraint("fk_vehicles_company_id", type_="foreignkey")
        batch_op.drop_column("company_id")
    op.drop_table("org_companies")
    op.drop_table("areas_districts")
    op.drop_table("regions")
