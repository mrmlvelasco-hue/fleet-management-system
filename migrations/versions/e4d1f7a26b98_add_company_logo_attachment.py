"""add company_profiles.logo_attachment_id

The configurable print letterhead logo. An ID rather than a blob:
CompanyProfile is read for every printed document, so a binary column
here would pull the image into memory on each one.

Revision ID: e4d1f7a26b98
Revises: d3c9e2f15a44
"""
from alembic import op
import sqlalchemy as sa

revision = "e4d1f7a26b98"
down_revision = "d3c9e2f15a44"
branch_labels = None
depends_on = None


def _columns(insp, table):
    return {c["name"] for c in insp.get_columns(table)}


def upgrade():
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if "company_profiles" not in set(insp.get_table_names()):
        return
    if "logo_attachment_id" not in _columns(insp, "company_profiles"):
        # Deliberately NOT a foreign key. Attachments are soft-deleted
        # via is_active, and a hard FK would block that path for a logo
        # that has been replaced -- turning "change the logo" into a
        # constraint error nobody expects.
        op.add_column("company_profiles",
                      sa.Column("logo_attachment_id", sa.Integer(),
                                nullable=True))


def downgrade():
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if ("company_profiles" in set(insp.get_table_names())
            and "logo_attachment_id" in _columns(insp, "company_profiles")):
        op.drop_column("company_profiles", "logo_attachment_id")
