"""Company Profile: login page picture (uploaded by the client's admin)

Revision ID: c9d2e4f7a1b8
Revises: b3e8f1a6c2d4
Create Date: 2026-10-08 12:00:00.000000

Two nullable columns mirroring the logo's. NULL = use the bundled default
picture, so every existing install keeps today's login page.
"""
from alembic import op
import sqlalchemy as sa


revision = "c9d2e4f7a1b8"
down_revision = "b3e8f1a6c2d4"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("company_profiles", schema=None) as batch_op:
        batch_op.add_column(sa.Column("login_picture_attachment_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("login_picture_filename", sa.String(length=255), nullable=True))


def downgrade():
    with op.batch_alter_table("company_profiles", schema=None) as batch_op:
        batch_op.drop_column("login_picture_filename")
        batch_op.drop_column("login_picture_attachment_id")
