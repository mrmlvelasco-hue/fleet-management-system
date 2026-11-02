"""MO Transaction Type -> Vehicle Master Data update mapping

Revision ID: c7a92e4f1d36
Revises: b3d8e1f6a4c7
Create Date: 2026-11-02 00:00:00.000000

Two new, fully additive tables for the "Configuration-Driven Vehicle
Master Data Updates Through Maintenance Orders" feature. No existing
table is altered.

- mo_transaction_type_field_mappings: config -- which Vehicle Master
  Data field a Transaction Type writes to on MO completion (optional,
  one-to-many from mo_transaction_types so a future type can map more
  than one field without another migration).
- maintenance_order_vehicle_changes: the proposed/applied change detail
  carried by a Maintenance Order (From/To, status, who/when applied).
  order_id is the traceability back to the MO's own document_number.

Both tables are empty on every existing install until an admin
actively configures a mapping, so this migration changes no observable
behaviour by itself.
"""
from alembic import op
import sqlalchemy as sa


revision = "c7a92e4f1d36"
down_revision = "b3d8e1f6a4c7"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "mo_transaction_type_field_mappings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False,
                  server_default=sa.true()),
        sa.Column("transaction_type_id", sa.Integer(), nullable=False),
        sa.Column("target_module", sa.String(length=40), nullable=False,
                  server_default="VEHICLE_MASTER_DATA"),
        sa.Column("target_field", sa.String(length=60), nullable=False),
        sa.Column("display_label", sa.String(length=120), nullable=False),
        sa.Column("input_type", sa.String(length=20), nullable=False,
                  server_default="TEXT"),
        sa.Column("select_source", sa.String(length=60), nullable=True),
        sa.Column("is_required", sa.Boolean(), nullable=False,
                  server_default=sa.true()),
        sa.Column("validation_rule", sa.String(length=60), nullable=True),
        sa.ForeignKeyConstraint(["transaction_type_id"],
                                ["mo_transaction_types.id"],
                                name="fk_mottfm_transaction_type"),
    )
    with op.batch_alter_table("mo_transaction_type_field_mappings",
                              schema=None) as batch_op:
        batch_op.create_index(
            "ix_mottfm_transaction_type_id", ["transaction_type_id"])

    op.create_table(
        "maintenance_order_vehicle_changes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False,
                  server_default=sa.true()),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("vehicle_id", sa.Integer(), nullable=False),
        sa.Column("transaction_type_id", sa.Integer(), nullable=True),
        sa.Column("mapping_id", sa.Integer(), nullable=True),
        sa.Column("target_field", sa.String(length=60), nullable=False),
        sa.Column("display_label", sa.String(length=120), nullable=False),
        sa.Column("old_value", sa.String(length=255), nullable=True),
        sa.Column("new_value", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=12), nullable=False,
                  server_default="PENDING"),
        sa.Column("applied_by", sa.Integer(), nullable=True),
        sa.Column("applied_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["order_id"], ["maintenance_orders.id"],
                                name="fk_movc_order"),
        sa.ForeignKeyConstraint(["vehicle_id"], ["vehicles.id"],
                                name="fk_movc_vehicle"),
        sa.ForeignKeyConstraint(["transaction_type_id"],
                                ["mo_transaction_types.id"],
                                name="fk_movc_transaction_type"),
        sa.ForeignKeyConstraint(["mapping_id"],
                                ["mo_transaction_type_field_mappings.id"],
                                name="fk_movc_mapping"),
        sa.ForeignKeyConstraint(["applied_by"], ["users.id"],
                                name="fk_movc_applied_by"),
    )
    with op.batch_alter_table("maintenance_order_vehicle_changes",
                              schema=None) as batch_op:
        batch_op.create_index("ix_movc_order_id", ["order_id"])
        batch_op.create_index("ix_movc_vehicle_id", ["vehicle_id"])


def downgrade():
    op.drop_table("maintenance_order_vehicle_changes")
    op.drop_table("mo_transaction_type_field_mappings")
