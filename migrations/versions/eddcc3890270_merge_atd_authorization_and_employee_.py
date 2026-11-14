"""merge ATD authorization and employee-number heads

Revision ID: eddcc3890270
Revises: 8bcaf47ad0c8, a3f6c9d12e47
Create Date: 2026-11-14 09:35:16.464800

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'eddcc3890270'
down_revision = ('8bcaf47ad0c8', 'a3f6c9d12e47')
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
