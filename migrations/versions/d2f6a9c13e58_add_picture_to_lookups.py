"""Add picture_attachment_id / picture_filename to lookups

Revision ID: d2f6a9c13e58
Revises: c7a92e4f1d36
Create Date: 2026-11-02 00:00:00.000000

Lets a picture be attached to any Lookup row through the existing
AttachmentService, same pattern as CompanyProfile.logo_attachment_id.
First use: the Vehicle Body Type lookup's picture, resolved per
vehicle and shown on the Handover Checklist's diagram (Michael,
2026-11-02).

Both columns are nullable and default to NULL, so every existing row
is unaffected until an admin uploads a picture.
"""
from alembic import op
import sqlalchemy as sa


revision = "d2f6a9c13e58"
down_revision = "c7a92e4f1d36"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("lookups",
                  sa.Column("picture_attachment_id", sa.Integer(),
                            nullable=True))
    op.add_column("lookups",
                  sa.Column("picture_filename", sa.String(length=255),
                            nullable=True))


def downgrade():
    op.drop_column("lookups", "picture_filename")
    op.drop_column("lookups", "picture_attachment_id")
