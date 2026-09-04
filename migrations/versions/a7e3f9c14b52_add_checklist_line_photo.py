"""add vehicle_checklist_lines.photo_attachment_id

Evidence for an ATTENTION or FAILED response, captured on the phone.

An id rather than bytes: this table holds a row per checklist ITEM (45
on the BLOWBAGETS template), so a blob column would be read on every
line of every inspection.

Revision ID: a7e3f9c14b52
Revises: f5a2c8b31d70
"""
from alembic import op
import sqlalchemy as sa

revision = "a7e3f9c14b52"
down_revision = "f5a2c8b31d70"
branch_labels = None
depends_on = None


def _columns(insp, table):
    return {c["name"] for c in insp.get_columns(table)}


def upgrade():
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if "vehicle_checklist_lines" not in set(insp.get_table_names()):
        return
    if "photo_attachment_id" not in _columns(insp, "vehicle_checklist_lines"):
        # Not a foreign key: attachments are soft-deleted via is_active,
        # and a hard FK would block that for a photo that has been
        # replaced -- turning "retake the picture" into a constraint
        # error. Same reasoning as company_profiles.logo_attachment_id.
        op.add_column("vehicle_checklist_lines",
                      sa.Column("photo_attachment_id", sa.Integer(),
                                nullable=True))


def downgrade():
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if ("vehicle_checklist_lines" in set(insp.get_table_names())
            and "photo_attachment_id" in _columns(insp,
                                                  "vehicle_checklist_lines")):
        op.drop_column("vehicle_checklist_lines", "photo_attachment_id")
