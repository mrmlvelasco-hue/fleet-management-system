"""add vehicle handover checklist tables

Revision ID: d2f6a8c14e97
Revises: b7e4c19f2d83
Create Date: 2026-09-12

New, standalone module (app/modules/transactions/vehicle_handover).
Deliberately independent of checklist_templates / checklist_categories /
checklist_items / checklist_documents: this is the alternative issuance/
return path that must keep working even if a client's contract excludes
the periodic-inspection checklist module and the mobile field APK
entirely. No foreign key in this migration references any of those
tables.

Three tables:
  * vehicle_handover_checklists -- one row per issuance/return cycle
  * vehicle_handover_items      -- the accessory checklist, two marks
                                    per row (issuance AND return)
  * vehicle_handover_damage_marks -- body-diagram marks, position
                                    stored as percentages (0-100) so a
                                    mark holds its place at any scale
"""
import sqlalchemy as sa
from alembic import op

revision = "d2f6a8c14e97"
down_revision = "b7e4c19f2d83"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "vehicle_handover_checklists",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("document_number", sa.String(40), unique=True, nullable=True),
        sa.Column("vehicle_id", sa.Integer(),
                 sa.ForeignKey("vehicles.id"), nullable=False, index=True),
        sa.Column("branch_id", sa.Integer(),
                 sa.ForeignKey("branches.id"), nullable=True, index=True),
        sa.Column("assignee_driver_id", sa.Integer(),
                 sa.ForeignKey("drivers.id"), nullable=True),
        sa.Column("assignee_name", sa.String(160), nullable=True),
        sa.Column("position", sa.String(120), nullable=True),
        sa.Column("company_bu", sa.String(120), nullable=True),
        sa.Column("status", sa.String(12), nullable=False,
                 server_default="DRAFT"),
        sa.Column("date_issued", sa.Date(), nullable=True),
        sa.Column("odometer_at_issuance", sa.Integer(), nullable=True),
        sa.Column("issued_by_name", sa.String(160), nullable=True),
        sa.Column("issued_by_date", sa.Date(), nullable=True),
        sa.Column("issuance_received_by_name", sa.String(160), nullable=True),
        sa.Column("issuance_received_by_date", sa.Date(), nullable=True),
        sa.Column("date_returned", sa.Date(), nullable=True),
        sa.Column("odometer_at_return", sa.Integer(), nullable=True),
        sa.Column("returned_by_name", sa.String(160), nullable=True),
        sa.Column("returned_by_date", sa.Date(), nullable=True),
        sa.Column("return_received_by_name", sa.String(160), nullable=True),
        sa.Column("return_received_by_date", sa.Date(), nullable=True),
        sa.Column("fuel_level", sa.Integer(), nullable=True),
        sa.Column("documents_json", sa.JSON(), nullable=True),
        sa.Column("other_items_json", sa.JSON(), nullable=True),
        sa.Column("remarks", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False,
                 server_default="1"),
    )

    op.create_table(
        "vehicle_handover_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("handover_id", sa.Integer(),
                 sa.ForeignKey("vehicle_handover_checklists.id"),
                 nullable=False, index=True),
        sa.Column("item_name", sa.String(120), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False,
                 server_default="0"),
        sa.Column("issuance_mark", sa.String(10), nullable=True),
        sa.Column("issuance_qty", sa.Integer(), nullable=True),
        sa.Column("return_mark", sa.String(10), nullable=True),
        sa.Column("return_qty", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False,
                 server_default="1"),
    )

    op.create_table(
        "vehicle_handover_damage_marks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("handover_id", sa.Integer(),
                 sa.ForeignKey("vehicle_handover_checklists.id"),
                 nullable=False, index=True),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("x", sa.Float(), nullable=False),
        sa.Column("y", sa.Float(), nullable=False),
        sa.Column("note", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False,
                 server_default="1"),
    )


def downgrade():
    op.drop_table("vehicle_handover_damage_marks")
    op.drop_table("vehicle_handover_items")
    op.drop_table("vehicle_handover_checklists")
