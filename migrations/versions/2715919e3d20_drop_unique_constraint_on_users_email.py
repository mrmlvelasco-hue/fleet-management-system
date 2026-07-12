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
    # Login is by username, not email — duplicate emails are safe. This
    # constraint previously caused an unhandled IntegrityError crash when
    # two users shared an email address.
    #
    # SQLite can't DROP an unnamed/autoindex constraint directly (no native
    # DROP CONSTRAINT support at all), so it needs the recreate-table
    # workaround below. MySQL (and most other real databases) support
    # native ALTER TABLE DROP constraint/index — and critically, forcing
    # the SQLite-style full table recreate on MySQL is actively harmful:
    # it issues a bare DROP TABLE, which fails outright as soon as any
    # other table (e.g. user_roles) holds a live foreign key to users.
    # So: dialect-aware. Native drop for everything except SQLite.
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
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
    else:
        # Find whatever this DB actually named the unique constraint on
        # `email` (created with no explicit name in the original phase 1a
        # migration, so the exact name is dialect-assigned) and drop it by
        # that real name, rather than guessing.
        inspector = sa.inspect(bind)
        for uc in inspector.get_unique_constraints("users"):
            if uc["column_names"] == ["email"]:
                op.drop_constraint(uc["name"], "users", type_="unique")
                break
        else:
            # Some MySQL versions/configs surface it as a unique INDEX
            # rather than a named UNIQUE CONSTRAINT — check there too.
            for ix in inspector.get_indexes("users"):
                if ix["unique"] and ix["column_names"] == ["email"]:
                    op.drop_index(ix["name"], table_name="users")
                    break


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table('users', schema=None) as batch_op:
            batch_op.create_unique_constraint('uq_users_email', ['email'])
    else:
        op.create_unique_constraint('uq_users_email', 'users', ['email'])
