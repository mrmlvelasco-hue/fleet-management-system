"""add mobile_app_releases and mobile_app_release_files

Phase 4. APK release management.

Two tables rather than one with a blob column: the version check runs on
every app start and the release list is an ordinary admin screen, so a
blob on the metadata table would risk pulling 20 MB per row into memory
on both. Splitting makes the binary a deliberate second fetch.

Revision ID: d3c9e2f15a44
Revises: c2b8d1e04f32
"""
from alembic import op
import sqlalchemy as sa

revision = "d3c9e2f15a44"
down_revision = "c2b8d1e04f32"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    tables = set(sa.inspect(conn).get_table_names())

    if "mobile_app_releases" not in tables:
        op.create_table(
            "mobile_app_releases",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("version_name", sa.String(length=40), nullable=False),
            sa.Column("version_code", sa.Integer(), nullable=False),
            sa.Column("platform", sa.String(length=20), nullable=False,
                      server_default="ANDROID"),
            sa.Column("status", sa.String(length=20), nullable=False,
                      server_default="DRAFT"),
            sa.Column("is_current", sa.Boolean(), nullable=False,
                      server_default=sa.text("0")),
            sa.Column("min_supported_version_code", sa.Integer(),
                      nullable=True),
            sa.Column("release_notes", sa.Text(), nullable=True),
            sa.Column("file_name", sa.String(length=255), nullable=True),
            sa.Column("file_size", sa.Integer(), nullable=True),
            sa.Column("checksum_sha256", sa.String(length=64), nullable=True),
            sa.Column("released_at", sa.DateTime(), nullable=True),
            # True once the binary has been deleted. Only the
            # latest APK is stored; the metadata row survives so
            # the version_code stays claimed and the history of
            # what was live when is not lost.
            sa.Column("file_purged", sa.Boolean(), nullable=False,
                      server_default=sa.text("0")),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.Column("created_by", sa.Integer(), nullable=True),
            sa.Column("updated_by", sa.Integer(), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False,
                      server_default=sa.text("1")),
        )
        op.create_index("ix_mobile_app_releases_version_code",
                        "mobile_app_releases", ["version_code"])

    if "mobile_app_release_files" not in tables:
        op.create_table(
            "mobile_app_release_files",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("release_id", sa.Integer(), nullable=False),
            # LargeBinary maps to LONGBLOB on MySQL. NOTE for
            # deployment: max_allowed_packet must exceed the APK size or
            # the INSERT fails at the server, not in Python -- the
            # SQLite suite cannot catch this.
            sa.Column("file_data", sa.LargeBinary(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.Column("created_by", sa.Integer(), nullable=True),
            sa.Column("updated_by", sa.Integer(), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False,
                      server_default=sa.text("1")),
            sa.ForeignKeyConstraint(["release_id"], ["mobile_app_releases.id"],
                                    name="fk_mobile_release_file_release"),
            sa.UniqueConstraint("release_id",
                                name="uq_mobile_release_file_release"),
        )


def downgrade():
    conn = op.get_bind()
    tables = set(sa.inspect(conn).get_table_names())
    if "mobile_app_release_files" in tables:
        op.drop_table("mobile_app_release_files")
    if "mobile_app_releases" in tables:
        op.drop_table("mobile_app_releases")
