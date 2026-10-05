"""Maintenance Order Scope of Work: manual lines on any order

Revision ID: c4e1a7b92d10
Revises: a1f9c3d8e6b2
Create Date: 2026-10-04 12:00:00.000000

maintenance_checklist_items already is the per-order Scope of Work
snapshot (copied from a PM Scope Template at creation). Two changes let
it hold hand-entered lines too:

  * activity_code becomes NULLABLE -- a manual line ("Repaint left
    door") usually has no activity code. Existing rows all have one, so
    relaxing the constraint loses nothing.
  * origin VARCHAR(10) NOT NULL DEFAULT 'TEMPLATE' -- TEMPLATE | MANUAL.
    The server default backfills every existing row as TEMPLATE, which
    is correct: before this revision a template copy was the ONLY way a
    line could be created.

batch_alter_table so the same migration runs on SQLite (tests/dev) as
well as MySQL 8.4 and SQL Server.

Downgrade: activity_code goes back to NOT NULL, so any manual line with
no code is given an empty-string code first rather than failing the
ALTER.
"""
from alembic import op
import sqlalchemy as sa


revision = "c4e1a7b92d10"
down_revision = "a1f9c3d8e6b2"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("maintenance_checklist_items",
                              schema=None) as batch_op:
        batch_op.alter_column("activity_code",
                              existing_type=sa.String(length=40),
                              nullable=True)
        batch_op.add_column(sa.Column(
            "origin", sa.String(length=10), nullable=False,
            server_default="TEMPLATE"))


def downgrade():
    items = sa.table("maintenance_checklist_items",
                     sa.column("activity_code", sa.String(40)))
    op.execute(items.update()
               .where(items.c.activity_code.is_(None))
               .values(activity_code=""))
    with op.batch_alter_table("maintenance_checklist_items",
                              schema=None) as batch_op:
        batch_op.drop_column("origin")
        batch_op.alter_column("activity_code",
                              existing_type=sa.String(length=40),
                              nullable=False)
