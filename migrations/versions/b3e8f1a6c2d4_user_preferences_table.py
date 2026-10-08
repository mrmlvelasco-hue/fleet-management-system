"""user_preferences table (per-user UI preferences, e.g. dashboard charts)

Revision ID: b3e8f1a6c2d4
Revises: a7c3d9e2f5b1
Create Date: 2026-10-07 11:00:00.000000

One row per (user, key); value is small JSON. Kept on the server so a
choice follows the user to every device (the mobile app renders the same
Dashboard). Rows are deleted with their user (ON DELETE CASCADE).
"""
from alembic import op
import sqlalchemy as sa


revision = "b3e8f1a6c2d4"
down_revision = "a7c3d9e2f5b1"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "user_preferences",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("pref_key", sa.String(length=80), nullable=False),
        sa.Column("value_json", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE",
                                name="fk_user_preferences_user"),
        sa.UniqueConstraint("user_id", "pref_key", name="uq_user_preference"),
    )
    op.create_index("ix_user_preferences_user_id", "user_preferences",
                    ["user_id"])


def downgrade():
    op.drop_index("ix_user_preferences_user_id", table_name="user_preferences")
    op.drop_table("user_preferences")
