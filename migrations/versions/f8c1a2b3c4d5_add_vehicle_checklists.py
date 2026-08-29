"""add vehicle checklist tables and CHK document type

Revision ID: f8c1a2b3c4d5
Revises: a7c419e35d02
Create Date: 2026-08-30 06:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "f8c1a2b3c4d5"
down_revision = "a7c419e35d02"
branch_labels = None
depends_on = None


def upgrade():
    insp = sa.inspect(op.get_bind())
    if "vehicle_checklists" in insp.get_table_names():
        return
    op.create_table(
        "checklist_templates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("vehicle_type_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.ForeignKeyConstraint(["vehicle_type_id"], ["vehicle_types.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "checklist_categories",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("template_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["template_id"], ["checklist_templates.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_checklist_categories_template_id", "checklist_categories", ["template_id"])
    op.create_table(
        "checklist_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("required", sa.Boolean(), nullable=False),
        sa.Column("photo_required_on_fail", sa.Boolean(), nullable=False),
        sa.Column("maintenance_allowed", sa.Boolean(), nullable=False),
        sa.Column("is_safety", sa.Boolean(), nullable=False),
        sa.Column("weight", sa.Integer(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.ForeignKeyConstraint(["category_id"], ["checklist_categories.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_checklist_items_category_id", "checklist_items", ["category_id"])
    op.create_table(
        "vehicle_checklists",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("document_number", sa.String(length=40), nullable=True),
        sa.Column("template_id", sa.Integer(), nullable=False),
        sa.Column("vehicle_id", sa.Integer(), nullable=False),
        sa.Column("driver_id", sa.Integer(), nullable=True),
        sa.Column("branch_id", sa.Integer(), nullable=True),
        sa.Column("odometer", sa.Integer(), nullable=True),
        sa.Column("inspection_date", sa.Date(), nullable=False),
        sa.Column("inspection_time", sa.Time(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("result", sa.String(length=20), nullable=True),
        sa.Column("score", sa.Numeric(5, 2), nullable=True),
        sa.Column("applicable_count", sa.Integer(), nullable=True),
        sa.Column("pass_count", sa.Integer(), nullable=True),
        sa.Column("attention_count", sa.Integer(), nullable=True),
        sa.Column("fail_count", sa.Integer(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(), nullable=True),
        sa.Column("submitted_by", sa.Integer(), nullable=True),
        sa.Column("remarks", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["template_id"], ["checklist_templates.id"]),
        sa.ForeignKeyConstraint(["vehicle_id"], ["vehicles.id"]),
        sa.ForeignKeyConstraint(["driver_id"], ["drivers.id"]),
        sa.ForeignKeyConstraint(["branch_id"], ["branches.id"]),
        sa.ForeignKeyConstraint(["submitted_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_number"),
    )
    op.create_index("ix_vehicle_checklists_vehicle_id", "vehicle_checklists", ["vehicle_id"])
    op.create_table(
        "vehicle_checklist_lines",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("checklist_id", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("category_name", sa.String(length=120), nullable=False),
        sa.Column("item_name", sa.String(length=150), nullable=False),
        sa.Column("response", sa.String(length=20), nullable=True),
        sa.Column("is_required", sa.Boolean(), nullable=False),
        sa.Column("is_safety", sa.Boolean(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["checklist_id"], ["vehicle_checklists.id"]),
        sa.ForeignKeyConstraint(["item_id"], ["checklist_items.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_vehicle_checklist_lines_checklist_id", "vehicle_checklist_lines", ["checklist_id"])
    op.create_table(
        "checklist_defects",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("checklist_id", sa.Integer(), nullable=False),
        sa.Column("line_id", sa.Integer(), nullable=True),
        sa.Column("item_name", sa.String(length=150), nullable=False),
        sa.Column("observation", sa.Text(), nullable=True),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("create_maintenance", sa.Boolean(), nullable=False),
        sa.Column("maintenance_order_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.ForeignKeyConstraint(["checklist_id"], ["vehicle_checklists.id"]),
        sa.ForeignKeyConstraint(["line_id"], ["vehicle_checklist_lines.id"]),
        sa.ForeignKeyConstraint(["maintenance_order_id"], ["maintenance_orders.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_checklist_defects_checklist_id", "checklist_defects", ["checklist_id"])

    conn = op.get_bind()
    exists = conn.execute(sa.text(
        "SELECT id FROM document_types WHERE code = 'CHK'"
    )).fetchone()
    if not exists:
        conn.execute(sa.text(
            """
            INSERT INTO document_types
              (code, name, description, requires_approval, auto_numbering,
               printable, mobile_available, attachment_allowed,
               created_at, updated_at, is_active)
            VALUES
              ('CHK', 'Vehicle Checklist', 'Daily / pre-trip vehicle inspection',
               0, 1, 1, 1, 1, NOW(), NOW(), 1)
            """
        ))
        dt = conn.execute(sa.text("SELECT id FROM document_types WHERE code='CHK'")).fetchone()
        if dt:
            conn.execute(sa.text(
                """
                INSERT INTO numbering_schemes
                  (document_type_id, prefix, suffix, include_year, include_month,
                   digit_count, separator, reset_policy,
                   created_at, updated_at, is_active)
                VALUES
                  (:dtid, 'CHK', '', 1, 0, 6, '-', 'YEARLY', NOW(), NOW(), 1)
                """
            ), {"dtid": dt[0]})


def downgrade():
    op.drop_table("checklist_defects")
    op.drop_table("vehicle_checklist_lines")
    op.drop_table("vehicle_checklists")
    op.drop_table("checklist_items")
    op.drop_table("checklist_categories")
    op.drop_table("checklist_templates")
