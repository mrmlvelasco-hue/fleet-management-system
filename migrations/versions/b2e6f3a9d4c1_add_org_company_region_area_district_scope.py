"""add ORG_COMPANY/ORG_REGION/ORG_AREA_DISTRICT scope columns + approval
instance company snapshot

Stage 3B (D4/D7) of the Fuel + Org master prompt. Adds the three new
nullable scope columns to user_org_scopes (company_id/region_id/
area_district_id) backing the ORG_COMPANY/ORG_REGION/ORG_AREA_DISTRICT
scope_type values, and a nullable company_id column on approval_instances
-- the same snapshot pattern branch_id/business_unit_id already use.
Non-destructive: new nullable columns only, nothing existing is altered
or backfilled. The legacy "COMPANY" scope_type value is untouched and
keeps meaning unrestricted, same as GLOBAL.

Revision ID: b2e6f3a9d4c1
Revises: 084f385f4149
Create Date: 2026-10-29 06:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "b2e6f3a9d4c1"
down_revision = "084f385f4149"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    insp = sa.inspect(conn)

    scope_cols = {c["name"] for c in insp.get_columns("user_org_scopes")}
    with op.batch_alter_table("user_org_scopes") as batch_op:
        if "company_id" not in scope_cols:
            batch_op.add_column(
                sa.Column("company_id", sa.Integer(), nullable=True))
            batch_op.create_foreign_key(
                "fk_user_org_scopes_company_id", "org_companies",
                ["company_id"], ["id"])
        if "region_id" not in scope_cols:
            batch_op.add_column(
                sa.Column("region_id", sa.Integer(), nullable=True))
            batch_op.create_foreign_key(
                "fk_user_org_scopes_region_id", "regions",
                ["region_id"], ["id"])
        if "area_district_id" not in scope_cols:
            batch_op.add_column(
                sa.Column("area_district_id", sa.Integer(), nullable=True))
            batch_op.create_foreign_key(
                "fk_user_org_scopes_area_district_id", "areas_districts",
                ["area_district_id"], ["id"])

    instance_cols = {c["name"] for c in insp.get_columns("approval_instances")}
    if "company_id" not in instance_cols:
        with op.batch_alter_table("approval_instances") as batch_op:
            batch_op.add_column(
                sa.Column("company_id", sa.Integer(), nullable=True))
            batch_op.create_foreign_key(
                "fk_approval_instances_company_id", "org_companies",
                ["company_id"], ["id"])


def downgrade():
    with op.batch_alter_table("approval_instances") as batch_op:
        batch_op.drop_constraint("fk_approval_instances_company_id",
                                 type_="foreignkey")
        batch_op.drop_column("company_id")
    with op.batch_alter_table("user_org_scopes") as batch_op:
        batch_op.drop_constraint("fk_user_org_scopes_area_district_id",
                                 type_="foreignkey")
        batch_op.drop_column("area_district_id")
        batch_op.drop_constraint("fk_user_org_scopes_region_id",
                                 type_="foreignkey")
        batch_op.drop_column("region_id")
        batch_op.drop_constraint("fk_user_org_scopes_company_id",
                                 type_="foreignkey")
        batch_op.drop_column("company_id")
