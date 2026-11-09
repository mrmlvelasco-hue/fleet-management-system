"""Add Vehicle.or_number column

OR (Official Receipt) and CR (Certificate of Registration) are separate
LTO documents with separate numbers. The Vehicle master already carries
cr_number; or_number is added the same way so VehicleRegistration
completion can sync it alongside cr_number.

Revision ID: 77cfc37cfe4a
Revises: 5d31e3996290
Create Date: 2026-11-09 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '77cfc37cfe4a'
down_revision = '5d31e3996290'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('vehicles', schema=None) as batch_op:
        batch_op.add_column(sa.Column('or_number', sa.String(length=60), nullable=True))


def downgrade():
    with op.batch_alter_table('vehicles', schema=None) as batch_op:
        batch_op.drop_column('or_number')
