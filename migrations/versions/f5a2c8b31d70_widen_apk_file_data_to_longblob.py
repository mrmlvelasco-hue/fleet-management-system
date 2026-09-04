"""widen mobile_app_release_files.file_data to LONGBLOB on MySQL

The column was created as plain LargeBinary, which SQLAlchemy maps to
MySQL BLOB -- a 65,535 byte limit. A real APK is 10-30 MB, so every
upload failed at the server with:

    (1406, "Data too long for column 'file_data' at row 1")

while the SQLite test suite passed, because SQLite has no such limit.
attachments.file_data already used the LONGBLOB variant; this table did
not follow it.

MySQL-only. SQLite ignores the type change, which is why no test could
have caught this and why test_binary_columns_are_longblob_on_mysql now
asserts it at the METADATA level instead.

Revision ID: f5a2c8b31d70
Revises: e4d1f7a26b98
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "f5a2c8b31d70"
down_revision = "e4d1f7a26b98"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    if conn.dialect.name != "mysql":
        # SQLite stores blobs of any size and cannot ALTER a column
        # type in place. Nothing to do, and attempting it would break
        # the dev database.
        return
    if "mobile_app_release_files" not in set(
            sa.inspect(conn).get_table_names()):
        return
    op.alter_column("mobile_app_release_files", "file_data",
                    existing_type=sa.LargeBinary(),
                    type_=mysql.LONGBLOB(), nullable=False)


def downgrade():
    conn = op.get_bind()
    if conn.dialect.name != "mysql":
        return
    # Deliberately NOT narrowing back to BLOB: any release already
    # stored would be truncated to 64 KB, producing an APK that
    # downloads and then fails to install. Leaving the column wide is
    # harmless; shrinking it destroys data.
    return
