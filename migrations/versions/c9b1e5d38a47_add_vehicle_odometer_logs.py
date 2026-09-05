"""add vehicle_odometer_logs

Every odometer reading -- from the field app, the web, a telematics
integration -- overwrote Vehicle.current_odometer and left no trace.
There was no way to answer "what did the driver push, and when".

That matters beyond curiosity: current_odometer drives PM due status, so
a wrong reading silently changes when a vehicle is called in for
service.

Revision ID: c9b1e5d38a47
Revises: b8f4d2a71c93
"""
from alembic import op
import sqlalchemy as sa

revision = "c9b1e5d38a47"
down_revision = "b8f4d2a71c93"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    if "vehicle_odometer_logs" in set(sa.inspect(conn).get_table_names()):
        return
    op.create_table(
        "vehicle_odometer_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("vehicle_id", sa.Integer(), nullable=False),
        sa.Column("reading", sa.Integer(), nullable=False),
        sa.Column("previous_reading", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(length=20), nullable=False,
                  server_default="WEB"),
        sa.Column("source_table", sa.String(length=50), nullable=True),
        sa.Column("source_id", sa.Integer(), nullable=True),
        sa.Column("recorded_at", sa.DateTime(), nullable=False),
        sa.Column("recorded_by", sa.Integer(), nullable=True),
        sa.Column("remarks", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False,
                  server_default=sa.text("1")),
        sa.ForeignKeyConstraint(["vehicle_id"], ["vehicles.id"],
                                name="fk_odo_log_vehicle"),
        sa.ForeignKeyConstraint(["recorded_by"], ["users.id"],
                                name="fk_odo_log_user"),
    )
    # Indexed from the start: at a daily reading per vehicle this grows
    # like the checklist table, and the checklist list had to be fixed
    # after the fact for exactly this omission.
    op.create_index("ix_odo_log_vehicle_id", "vehicle_odometer_logs",
                    ["vehicle_id"])
    op.create_index("ix_odo_log_recorded_at", "vehicle_odometer_logs",
                    ["recorded_at"])
    op.create_index("ix_odo_log_source", "vehicle_odometer_logs", ["source"])
    op.create_index("ix_odo_log_recorded_by", "vehicle_odometer_logs",
                    ["recorded_by"])


def downgrade():
    conn = op.get_bind()
    if "vehicle_odometer_logs" in set(sa.inspect(conn).get_table_names()):
        op.drop_table("vehicle_odometer_logs")
