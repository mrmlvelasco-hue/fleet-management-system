"""Standalone tire / battery REPLACE as one document (2026-10-19)

Revision ID: e8b4f0d2c6a3
Revises: d7a3e9c1b5f2
Create Date: 2026-10-19 10:00:00.000000

Additive. tire_id / battery_id become nullable: a REPLACE whose new item
is supplied by the workshop has no master record until the document
completes (it is registered then). Every existing row already has one, so
nothing existing changes. Plus removed item / reason / destination,
install source and new_* fields, workshop, technician, labour cost, and a
Scope of Work table per module.
"""
from alembic import op
import sqlalchemy as sa


revision = "e8b4f0d2c6a3"
down_revision = "d7a3e9c1b5f2"
branch_labels = None
depends_on = None


def _cols(insp, table):
    return {c["name"] for c in insp.get_columns(table)}


def _common(prefix):
    return [
        sa.Column(f"removed_{prefix}_id", sa.Integer(), nullable=True),
        sa.Column("removal_reason", sa.String(length=40), nullable=True),
        sa.Column("removed_to", sa.String(length=15), nullable=True),
        sa.Column("install_source", sa.String(length=10), nullable=True),
        sa.Column("new_serial", sa.String(length=60), nullable=True),
        sa.Column("new_brand", sa.String(length=80), nullable=True),
        sa.Column("new_model", sa.String(length=80), nullable=True),
        sa.Column("vendor_id", sa.Integer(), nullable=True),
        sa.Column("technician", sa.String(length=120), nullable=True),
        sa.Column("labor_cost", sa.Numeric(18, 2), nullable=True),
    ]


def _scope_table(name, parent):
    op.create_table(
        name,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("transaction_id", sa.Integer(), nullable=False),
        sa.Column("activity_description", sa.String(length=255),
                  nullable=False),
        sa.Column("is_done", sa.Boolean(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["transaction_id"], [f"{parent}.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(f"ix_{name}_transaction_id", name, ["transaction_id"],
                    unique=False)


def upgrade():
    conn = op.get_bind()
    insp = sa.inspect(conn)
    for table, prefix, master, extra in (
            ("tire_transactions", "tire", "tires",
             [sa.Column("new_size", sa.String(length=30), nullable=True)]),
            ("battery_transactions", "battery", "batteries",
             [sa.Column("new_warranty_months", sa.Integer(),
                        nullable=True)])):
        have = _cols(insp, table)
        todo = [c for c in _common(prefix) + extra if c.name not in have]
        with op.batch_alter_table(table, schema=None) as b:
            b.alter_column(f"{prefix}_id", existing_type=sa.Integer(),
                           nullable=True)
            for c in todo:
                b.add_column(c)
            if f"removed_{prefix}_id" not in have:
                b.create_foreign_key(f"fk_{table}_removed", master,
                                     [f"removed_{prefix}_id"], ["id"])
                b.create_foreign_key(f"fk_{table}_vendor", "vendors",
                                     ["vendor_id"], ["id"])
    tables = set(sa.inspect(conn).get_table_names())
    if "tire_transaction_scope_items" not in tables:
        _scope_table("tire_transaction_scope_items", "tire_transactions")
    if "battery_transaction_scope_items" not in tables:
        _scope_table("battery_transaction_scope_items",
                     "battery_transactions")


def downgrade():
    for name in ("battery_transaction_scope_items",
                 "tire_transaction_scope_items"):
        op.drop_index(f"ix_{name}_transaction_id", table_name=name)
        op.drop_table(name)
    for table, prefix, extra in (
            ("battery_transactions", "battery", "new_warranty_months"),
            ("tire_transactions", "tire", "new_size")):
        with op.batch_alter_table(table, schema=None) as b:
            b.drop_constraint(f"fk_{table}_vendor", type_="foreignkey")
            b.drop_constraint(f"fk_{table}_removed", type_="foreignkey")
            for c in (f"removed_{prefix}_id", "removal_reason", "removed_to",
                      "install_source", "new_serial", "new_brand",
                      "new_model", extra, "vendor_id", "technician",
                      "labor_cost"):
                b.drop_column(c)
            # Only safe when no REPLACE draft is waiting with no item yet.
            b.alter_column(f"{prefix}_id", existing_type=sa.Integer(),
                           nullable=False)
