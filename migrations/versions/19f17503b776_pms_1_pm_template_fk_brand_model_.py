"""PMS-1: PM Template FK brand/model matching, variant/engine/fuel/transmission/year-range/profile fields, vehicle master PMS fields

Revision ID: 19f17503b776
Revises: 69830f508e7b
Create Date: 2026-07-13 07:16:16.193509

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '19f17503b776'
down_revision = '69830f508e7b'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('pm_schedules', schema=None) as batch_op:
        batch_op.add_column(sa.Column('vehicle_brand_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('vehicle_model_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('variant', sa.String(length=80), nullable=True))
        batch_op.add_column(sa.Column('engine_type', sa.String(length=80), nullable=True))
        batch_op.add_column(sa.Column('fuel_type', sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column('transmission', sa.String(length=40), nullable=True))
        batch_op.add_column(sa.Column('model_year_from', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('model_year_to', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('profile_code', sa.String(length=40), nullable=True))
        batch_op.add_column(sa.Column('profile_description', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('effective_date', sa.Date(), nullable=True))
        batch_op.create_unique_constraint('uq_pm_schedules_profile_code', ['profile_code'])
        batch_op.create_foreign_key('fk_pm_schedules_vehicle_brand_id',
                                    'vehicle_brands', ['vehicle_brand_id'], ['id'])
        batch_op.create_foreign_key('fk_pm_schedules_vehicle_model_id',
                                    'vehicle_models', ['vehicle_model_id'], ['id'])

    with op.batch_alter_table('vehicles', schema=None) as batch_op:
        batch_op.add_column(sa.Column('variant', sa.String(length=80), nullable=True))
        batch_op.add_column(sa.Column('engine_type', sa.String(length=80), nullable=True))
        batch_op.add_column(sa.Column('transmission', sa.String(length=40), nullable=True))
        batch_op.add_column(sa.Column('current_engine_hours', sa.Integer(), nullable=True))


def downgrade():
    with op.batch_alter_table('vehicles', schema=None) as batch_op:
        batch_op.drop_column('current_engine_hours')
        batch_op.drop_column('transmission')
        batch_op.drop_column('engine_type')
        batch_op.drop_column('variant')

    with op.batch_alter_table('pm_schedules', schema=None) as batch_op:
        batch_op.drop_constraint('fk_pm_schedules_vehicle_brand_id', type_='foreignkey')
        batch_op.drop_constraint('fk_pm_schedules_vehicle_model_id', type_='foreignkey')
        batch_op.drop_constraint('uq_pm_schedules_profile_code', type_='unique')
        batch_op.drop_column('effective_date')
        batch_op.drop_column('profile_description')
        batch_op.drop_column('profile_code')
        batch_op.drop_column('model_year_to')
        batch_op.drop_column('model_year_from')
        batch_op.drop_column('transmission')
        batch_op.drop_column('fuel_type')
        batch_op.drop_column('engine_type')
        batch_op.drop_column('variant')
        batch_op.drop_column('vehicle_model_id')
        batch_op.drop_column('vehicle_brand_id')
