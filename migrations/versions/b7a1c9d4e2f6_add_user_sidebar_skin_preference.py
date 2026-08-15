"""add per-user sidebar appearance preference

Adds users.sidebar_skin, holding which sidebar look a person chose for
themselves. Nullable on purpose: NULL means "no personal choice", which
resolves to the company-wide SIDEBAR_SKIN_DEFAULT system parameter.

That is deliberately NOT the same as backfilling every existing row with
'classic'. Backfilling would pin every current user to Classic forever,
so if the client later set a different house style nobody would ever see
it -- they would all look like they had explicitly opted out. Leaving
the column NULL means existing users keep seeing exactly what they see
today (the default resolves to classic), but they follow the house style
if it is ever changed.

Revision ID: b7a1c9d4e2f6
Revises: 4e2f3f80f518
Create Date: 2026-08-15

"""
from alembic import op
import sqlalchemy as sa


revision = 'b7a1c9d4e2f6'
down_revision = '4e2f3f80f518'
branch_labels = None
depends_on = None


def upgrade():
    # batch_alter_table so this also works on SQLite, which cannot ALTER
    # a table to add a column with constraints in place -- the test suite
    # runs on SQLite while production is MySQL.
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('sidebar_skin', sa.String(length=30), nullable=True))


def downgrade():
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('sidebar_skin')
