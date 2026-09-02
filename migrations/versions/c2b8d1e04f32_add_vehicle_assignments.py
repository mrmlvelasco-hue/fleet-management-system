"""add vehicle_assignments history table and backfill current assignments

Phase 1b. Assignment becomes a dated, sourced history instead of a
single overwritten column.

`vehicles.assigned_driver_id` is deliberately NOT dropped, altered or
renamed. It stays a real column, maintained by VehicleAssignmentService
as a cache of the open row, so every existing SQL filter, template read
and API payload keeps working with no change. Nothing that reads it
today behaves differently after this migration.

The backfill inserts one OPEN row per currently-assigned vehicle, with
`assigned_from` NULL and source BACKFILL. NULL honestly means "in force,
start unknown" -- inventing a plausible start date would put fiction
into an audit table, and nobody reading it later could tell which dates
were real.

Revision ID: c2b8d1e04f32
Revises: b1a7c0d93e21
Create Date: 2026-09-03 10:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "c2b8d1e04f32"
down_revision = "b1a7c0d93e21"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    insp = sa.inspect(conn)
    tables = set(insp.get_table_names())

    if "vehicle_assignments" not in tables:
        op.create_table(
            "vehicle_assignments",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("vehicle_id", sa.Integer(), nullable=False),
            sa.Column("driver_id", sa.Integer(), nullable=False),
            sa.Column("assigned_from", sa.Date(), nullable=True),
            sa.Column("assigned_to", sa.Date(), nullable=True),
            sa.Column("source", sa.String(length=20), nullable=False,
                      server_default="MANUAL"),
            sa.Column("source_table", sa.String(length=50), nullable=True),
            sa.Column("source_id", sa.Integer(), nullable=True),
            sa.Column("remarks", sa.Text(), nullable=True),
            # BaseModel's audit columns, spelled out because Alembic sees
            # the table, not the mixin.
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.Column("created_by", sa.Integer(), nullable=True),
            sa.Column("updated_by", sa.Integer(), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False,
                      server_default=sa.text("1")),
            sa.ForeignKeyConstraint(["vehicle_id"], ["vehicles.id"],
                                    name="fk_vehicle_assignments_vehicle"),
            sa.ForeignKeyConstraint(["driver_id"], ["drivers.id"],
                                    name="fk_vehicle_assignments_driver"),
        )
        op.create_index("ix_vehicle_assignments_vehicle_id",
                        "vehicle_assignments", ["vehicle_id"])
        op.create_index("ix_vehicle_assignments_driver_id",
                        "vehicle_assignments", ["driver_id"])
        # `assigned_to IS NULL` is THE predicate for "currently held" and
        # every scope decision in Phase 2 rests on it, so it is indexed
        # from the start rather than after the first slow query.
        op.create_index("ix_vehicle_assignments_assigned_to",
                        "vehicle_assignments", ["assigned_to"])

    _backfill(conn)


def _backfill(conn):
    """One open row per currently-assigned vehicle.

    Guarded against re-running: a second pass must not double-open every
    assignment. The guard is "does this vehicle already have an open
    row", not "is the table empty", so a database that was partially
    backfilled during troubleshooting still completes correctly.
    """
    insp = sa.inspect(conn)
    if "vehicles" not in set(insp.get_table_names()):
        return
    columns = {c["name"] for c in insp.get_columns("vehicles")}
    if "assigned_driver_id" not in columns:
        return

    conn.execute(sa.text(
        """
        INSERT INTO vehicle_assignments
          (vehicle_id, driver_id, assigned_from, assigned_to, source,
           created_at, updated_at, is_active)
        SELECT v.id, v.assigned_driver_id, NULL, NULL, 'BACKFILL',
               CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 1
        FROM vehicles v
        WHERE v.assigned_driver_id IS NOT NULL
          AND NOT EXISTS (
            SELECT 1 FROM vehicle_assignments a
            WHERE a.vehicle_id = v.id AND a.assigned_to IS NULL
          )
        """
    ))


def downgrade():
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if "vehicle_assignments" in set(insp.get_table_names()):
        # vehicles.assigned_driver_id was never touched on the way up, so
        # dropping this table returns the system exactly to its previous
        # behaviour -- the history is lost, but nothing breaks.
        op.drop_table("vehicle_assignments")
