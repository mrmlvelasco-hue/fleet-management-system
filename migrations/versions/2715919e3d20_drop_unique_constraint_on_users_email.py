"""drop unique constraint on users.email

Revision ID: 2715919e3d20
Revises: 790396a1bd15
Create Date: 2026-07-12 08:46:01.570138

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '2715919e3d20'
down_revision = '790396a1bd15'
branch_labels = None
depends_on = None


def upgrade():
    # SQLite can't DROP an unnamed/autoindex constraint directly, and
    # autogenerate doesn't reliably diff anonymous UNIQUE constraints for
    # SQLite either. The standard fix: give batch mode an explicit target
    # schema (without the email uniqueness) via copy_from, so it rebuilds
    # the table correctly. Login is by username, not email, so duplicate
    # emails are safe — this constraint previously caused an unhandled
    # IntegrityError crash when two users shared an email address.
    target_users = sa.Table(
        'users', sa.MetaData(),
        sa.Column('username', sa.String(80), nullable=False),
        sa.Column('email', sa.String(255), nullable=False),
        sa.Column('password_hash', sa.String(255), nullable=False),
        sa.Column('first_name', sa.String(80)),
        sa.Column('last_name', sa.String(80)),
        sa.Column('branch_id', sa.Integer()),
        sa.Column('last_login_at', sa.DateTime()),
        sa.Column('failed_login_attempts', sa.Integer(), nullable=False),
        sa.Column('must_change_password', sa.Boolean(), nullable=False),
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('created_by', sa.Integer()),
        sa.Column('updated_by', sa.Integer()),
        sa.Column('is_active', sa.Boolean(), nullable=False),
    )
    with op.batch_alter_table('users', schema=None, copy_from=target_users,
                              recreate='always'):
        pass
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.create_index('ix_users_username', ['username'], unique=True)


def downgrade():
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.create_unique_constraint('uq_users_email', ['email'])
