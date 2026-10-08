"""System parameter SESSION_REMEMBER_POLICY (default ALWAYS_TIMEOUT)

Revision ID: a7c3d9e2f5b1
Revises: f1b4e7c2a903
Create Date: 2026-10-07 09:00:00.000000

Seeding skips parameters that already exist and never adds new ones to a
running database, so without this the new policy would be invisible in
System Administration -> System Parameters on existing installs (the app
would still behave as ALWAYS_TIMEOUT, its safe fallback, but there would
be nothing to switch).

  ALWAYS_TIMEOUT   (default) sign in again after SESSION_TIMEOUT_MINUTES
                   of inactivity, even if the browser was closed;
                   "Remember me" only remembers the username.
  REMEMBER_EXTENDS "Remember me" keeps the user signed in up to 14 days
                   (the previous behaviour).

Idempotent: inserts only when the row is absent. Downgrade removes it.
"""
from datetime import datetime

from alembic import op
import sqlalchemy as sa


revision = "a7c3d9e2f5b1"
down_revision = "f1b4e7c2a903"
branch_labels = None
depends_on = None

CODE = "SESSION_REMEMBER_POLICY"
DESCRIPTION = (
    "ALWAYS_TIMEOUT = sign in again after SESSION_TIMEOUT_MINUTES of "
    "inactivity, even if the browser was closed ('Remember me' only "
    "remembers the username). REMEMBER_EXTENDS = 'Remember me' keeps "
    "the user signed in for up to 14 days.")

_params = sa.table(
    "system_parameters",
    sa.column("code", sa.String),
    sa.column("value", sa.Text),
    sa.column("data_type", sa.String),
    sa.column("description", sa.String),
    sa.column("group_name", sa.String),
    sa.column("is_editable", sa.Boolean),
    sa.column("is_active", sa.Boolean),
    sa.column("created_at", sa.DateTime),
    sa.column("updated_at", sa.DateTime),
)


def upgrade():
    conn = op.get_bind()
    exists = conn.execute(
        sa.select(_params.c.code).where(_params.c.code == CODE)).first()
    if exists:
        return
    now = datetime.utcnow()
    conn.execute(_params.insert().values(
        code=CODE, value="ALWAYS_TIMEOUT", data_type="STRING",
        description=DESCRIPTION, group_name="SECURITY", is_editable=True,
        is_active=True, created_at=now, updated_at=now))


def downgrade():
    op.get_bind().execute(_params.delete().where(_params.c.code == CODE))
