"""add restrict_to_assigned_vehicles to users

Revision ID: b7e4c19f2d83
Revises: c9b1e5d38a47
Create Date: 2026-09-11

Opt-in narrowing of a user's vehicle visibility to the vehicles
currently assigned to them (open VehicleAssignment rows).

server_default="0" rather than a plain default: existing rows need a
value at the moment the column is added, and a Python-side default only
applies to objects SQLAlchemy creates. Without it the ALTER fails on
MySQL for a NOT NULL column on a populated table.

The default is FALSE, so running this migration restricts nobody. Access
changes only when an administrator ticks the box for a specific user.
"""
import sqlalchemy as sa
from alembic import op

revision = "b7e4c19f2d83"
down_revision = "c9b1e5d38a47"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "users",
        sa.Column("restrict_to_assigned_vehicles", sa.Boolean(),
                  nullable=False, server_default="0"),
    )


def downgrade():
    op.drop_column("users", "restrict_to_assigned_vehicles")
