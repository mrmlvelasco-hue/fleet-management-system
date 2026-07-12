"""vehicle assigned driver FK, vehicle movement enhancements (driver, employee responsible, purpose, start/end datetime)

Revision ID: a4d54ba9d40a
Revises: 2715919e3d20
Create Date: 2026-07-12 10:35:05.115923

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a4d54ba9d40a'
down_revision = '2715919e3d20'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('vehicle_movements', schema=None) as batch_op:
        batch_op.add_column(sa.Column('driver_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('employee_responsible', sa.String(length=120), nullable=True))
        batch_op.add_column(sa.Column('purpose', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('movement_start_datetime', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('movement_end_datetime', sa.DateTime(), nullable=True))
        batch_op.create_foreign_key('fk_vehicle_movements_driver_id',
                                    'drivers', ['driver_id'], ['id'])

    with op.batch_alter_table('vehicles', schema=None) as batch_op:
        batch_op.create_foreign_key('fk_vehicles_assigned_driver_id',
                                    'drivers', ['assigned_driver_id'], ['id'])


def downgrade():
    with op.batch_alter_table('vehicles', schema=None) as batch_op:
        batch_op.drop_constraint('fk_vehicles_assigned_driver_id', type_='foreignkey')

    with op.batch_alter_table('vehicle_movements', schema=None) as batch_op:
        batch_op.drop_constraint('fk_vehicle_movements_driver_id', type_='foreignkey')
        batch_op.drop_column('movement_end_datetime')
        batch_op.drop_column('movement_start_datetime')
        batch_op.drop_column('purpose')
        batch_op.drop_column('employee_responsible')
        batch_op.drop_column('driver_id')
