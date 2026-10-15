"""Tire & battery work on the Maintenance Order (2026-10-15)

Revision ID: d7a3e9c1b5f2
Revises: c9d2e4f7a1b8
Create Date: 2026-10-15 23:30:00.000000

Additive only -- every new column is nullable (or has a server default),
so existing tires, batteries, transactions and orders keep reading
exactly as before. Nothing historical is invented: old transactions stay
source MANUAL with no MO link, odometer or cost.

- maintenance_types.equipment_kind (TIRE | BATTERY), set for the existing
  PMS-TIRE / PMS-BATT codes.
- tires / batteries: model, mount date & odometer, last reading;
  batteries also warranty months.
- tire_transactions / battery_transactions: wheel position, readings,
  cost, source (MANUAL | MO | CHECKLIST), maintenance_order_id.
- maintenance_order_equipment_lines: the MO's Tire & Battery Work.
"""
from alembic import op
import sqlalchemy as sa


revision = "d7a3e9c1b5f2"
down_revision = "c9d2e4f7a1b8"
branch_labels = None
depends_on = None


def _cols(insp, table):
    return {c["name"] for c in insp.get_columns(table)}


def _add(insp, table, columns):
    have = _cols(insp, table)
    todo = [c for c in columns if c.name not in have]
    if not todo:
        return
    with op.batch_alter_table(table, schema=None) as b:
        for c in todo:
            b.add_column(c)


def upgrade():
    conn = op.get_bind()
    insp = sa.inspect(conn)

    _add(insp, "maintenance_types", [
        sa.Column("equipment_kind", sa.String(length=10), nullable=True)])
    conn.execute(sa.text(
        "UPDATE maintenance_types SET equipment_kind = 'TIRE' "
        "WHERE UPPER(code) = 'PMS-TIRE' AND equipment_kind IS NULL"))
    conn.execute(sa.text(
        "UPDATE maintenance_types SET equipment_kind = 'BATTERY' "
        "WHERE UPPER(code) = 'PMS-BATT' AND equipment_kind IS NULL"))

    _add(insp, "tires", [
        sa.Column("model", sa.String(length=80), nullable=True),
        sa.Column("current_tread_mm", sa.Numeric(5, 2), nullable=True),
        sa.Column("last_inspection_date", sa.Date(), nullable=True),
        sa.Column("mounted_date", sa.Date(), nullable=True),
        sa.Column("mounted_odometer", sa.Integer(), nullable=True)])
    _add(insp, "batteries", [
        sa.Column("model", sa.String(length=80), nullable=True),
        sa.Column("warranty_months", sa.Integer(), nullable=True),
        sa.Column("mounted_date", sa.Date(), nullable=True),
        sa.Column("mounted_odometer", sa.Integer(), nullable=True),
        sa.Column("last_voltage", sa.Numeric(5, 2), nullable=True),
        sa.Column("last_test_date", sa.Date(), nullable=True)])

    _add(insp, "tire_transactions", [
        sa.Column("wheel_position", sa.String(length=20), nullable=True),
        sa.Column("tread_depth_mm", sa.Numeric(5, 2), nullable=True),
        sa.Column("pressure_psi", sa.Integer(), nullable=True),
        sa.Column("cost", sa.Numeric(18, 2), nullable=True),
        sa.Column("source", sa.String(length=12), nullable=False,
                  server_default="MANUAL"),
        sa.Column("maintenance_order_id", sa.Integer(), nullable=True)])
    _add(insp, "battery_transactions", [
        sa.Column("odometer_at_service", sa.Integer(), nullable=True),
        sa.Column("voltage", sa.Numeric(5, 2), nullable=True),
        sa.Column("test_result", sa.String(length=10), nullable=True),
        sa.Column("cost", sa.Numeric(18, 2), nullable=True),
        sa.Column("source", sa.String(length=12), nullable=False,
                  server_default="MANUAL"),
        sa.Column("maintenance_order_id", sa.Integer(), nullable=True)])

    insp = sa.inspect(conn)
    for table in ("tire_transactions", "battery_transactions"):
        idx = {i["name"] for i in insp.get_indexes(table)}
        name = f"ix_{table}_maintenance_order_id"
        if name not in idx:
            with op.batch_alter_table(table, schema=None) as b:
                b.create_index(name, ["maintenance_order_id"], unique=False)
                b.create_foreign_key(f"fk_{table}_mo", "maintenance_orders",
                                     ["maintenance_order_id"], ["id"])

    if "maintenance_order_equipment_lines" not in set(insp.get_table_names()):
        op.create_table(
            "maintenance_order_equipment_lines",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.Column("created_by", sa.Integer(), nullable=True),
            sa.Column("updated_by", sa.Integer(), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False),
            sa.Column("order_id", sa.Integer(), nullable=False),
            sa.Column("kind", sa.String(length=10), nullable=False),
            sa.Column("position", sa.String(length=20), nullable=True),
            sa.Column("action", sa.String(length=10), nullable=False),
            sa.Column("rotate_to_position", sa.String(length=20),
                      nullable=True),
            sa.Column("current_tire_id", sa.Integer(), nullable=True),
            sa.Column("current_battery_id", sa.Integer(), nullable=True),
            sa.Column("current_description", sa.String(length=150),
                      nullable=True),
            sa.Column("install_source", sa.String(length=10), nullable=True),
            sa.Column("installed_tire_id", sa.Integer(), nullable=True),
            sa.Column("installed_battery_id", sa.Integer(), nullable=True),
            sa.Column("new_serial", sa.String(length=60), nullable=True),
            sa.Column("new_brand", sa.String(length=80), nullable=True),
            sa.Column("new_model", sa.String(length=80), nullable=True),
            sa.Column("new_size", sa.String(length=30), nullable=True),
            sa.Column("new_warranty_months", sa.Integer(), nullable=True),
            sa.Column("tread_depth_mm", sa.Numeric(5, 2), nullable=True),
            sa.Column("pressure_psi", sa.Integer(), nullable=True),
            sa.Column("voltage", sa.Numeric(5, 2), nullable=True),
            sa.Column("test_result", sa.String(length=10), nullable=True),
            sa.Column("status", sa.String(length=10), nullable=False),
            sa.Column("posted_at", sa.DateTime(), nullable=True),
            sa.Column("remarks", sa.String(length=255), nullable=True),
            sa.Column("sort_order", sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(["order_id"], ["maintenance_orders.id"]),
            sa.ForeignKeyConstraint(["current_tire_id"], ["tires.id"]),
            sa.ForeignKeyConstraint(["current_battery_id"], ["batteries.id"]),
            sa.ForeignKeyConstraint(["installed_tire_id"], ["tires.id"]),
            sa.ForeignKeyConstraint(["installed_battery_id"],
                                    ["batteries.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_maintenance_order_equipment_lines_order_id",
                        "maintenance_order_equipment_lines", ["order_id"],
                        unique=False)


def downgrade():
    op.drop_index("ix_maintenance_order_equipment_lines_order_id",
                  table_name="maintenance_order_equipment_lines")
    op.drop_table("maintenance_order_equipment_lines")
    for table in ("tire_transactions", "battery_transactions"):
        with op.batch_alter_table(table, schema=None) as b:
            b.drop_constraint(f"fk_{table}_mo", type_="foreignkey")
            b.drop_index(f"ix_{table}_maintenance_order_id")
    with op.batch_alter_table("battery_transactions", schema=None) as b:
        for c in ("maintenance_order_id", "source", "cost", "test_result",
                  "voltage", "odometer_at_service"):
            b.drop_column(c)
    with op.batch_alter_table("tire_transactions", schema=None) as b:
        for c in ("maintenance_order_id", "source", "cost", "pressure_psi",
                  "tread_depth_mm", "wheel_position"):
            b.drop_column(c)
    with op.batch_alter_table("batteries", schema=None) as b:
        for c in ("last_test_date", "last_voltage", "mounted_odometer",
                  "mounted_date", "warranty_months", "model"):
            b.drop_column(c)
    with op.batch_alter_table("tires", schema=None) as b:
        for c in ("mounted_odometer", "mounted_date", "last_inspection_date",
                  "current_tread_mm", "model"):
            b.drop_column(c)
    with op.batch_alter_table("maintenance_types", schema=None) as b:
        b.drop_column("equipment_kind")
