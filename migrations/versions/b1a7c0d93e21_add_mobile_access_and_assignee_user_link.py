"""add users.mobile_access and drivers.user_id

Phase 1a of the mobile programme. Two additive columns:

  users.mobile_access  -- may this account obtain a token from the
                          Android field app (a CHANNEL switch, not a
                          permission)
  drivers.user_id      -- THE link between a login and a vehicle
                          assignee

DEPLOYMENT NOTE, please read before applying.

mobile_access defaults to FALSE, which is the correct secure default and
means no account can obtain a native token until an administrator ticks
the box in User Maintenance. On an install where a field APK is already
in use, every phone stops logging in the moment this migration runs,
until the flag is set on those users. That is intended -- but it should
be scheduled, not discovered.

Revision ID: b1a7c0d93e21
Revises: f8c1a2b3c4d5
Create Date: 2026-09-03 09:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "b1a7c0d93e21"
down_revision = "f8c1a2b3c4d5"
branch_labels = None
depends_on = None


def _columns(insp, table):
    return {c["name"] for c in insp.get_columns(table)}


def upgrade():
    # Inspected before each change rather than assumed, matching the
    # style of the checklist migration. A database that was partially
    # upgraded (or hand-patched during an earlier troubleshooting
    # session, which has happened on this project) must still be able to
    # run this to completion instead of dying on a duplicate column.
    conn = op.get_bind()
    insp = sa.inspect(conn)
    tables = set(insp.get_table_names())

    if "users" in tables and "mobile_access" not in _columns(insp, "users"):
        # server_default is required, not optional: the column is NOT
        # NULL and existing rows have no value. Without it the ALTER
        # fails on every non-empty users table -- which is every real
        # install.
        op.add_column("users", sa.Column(
            "mobile_access", sa.Boolean(), nullable=False,
            server_default=sa.text("0")))
        # Dropped again immediately so the DEFAULT lives in the
        # application model, not in the schema. Leaving it behind means
        # a future change to the default silently disagrees with what
        # the database does for anyone inserting outside the ORM.
        op.alter_column("users", "mobile_access", server_default=None)

    if "drivers" in tables and "user_id" not in _columns(insp, "drivers"):
        op.add_column("drivers", sa.Column(
            "user_id", sa.Integer(), nullable=True))
        # Named explicitly. An anonymous UNIQUE constraint cannot be
        # dropped on MySQL without first discovering the name the server
        # invented for it, which turns a one-line downgrade into an
        # archaeology exercise.
        op.create_unique_constraint("uq_drivers_user_id", "drivers",
                                    ["user_id"])
        op.create_foreign_key("fk_drivers_user_id", "drivers", "users",
                              ["user_id"], ["id"])


def downgrade():
    conn = op.get_bind()
    insp = sa.inspect(conn)
    tables = set(insp.get_table_names())

    if "drivers" in tables and "user_id" in _columns(insp, "drivers"):
        # Order matters: the FK references the column the unique
        # constraint indexes, and MySQL refuses to drop an index a
        # foreign key still needs.
        op.drop_constraint("fk_drivers_user_id", "drivers",
                           type_="foreignkey")
        op.drop_constraint("uq_drivers_user_id", "drivers", type_="unique")
        op.drop_column("drivers", "user_id")

    if "users" in tables and "mobile_access" in _columns(insp, "users"):
        op.drop_column("users", "mobile_access")
