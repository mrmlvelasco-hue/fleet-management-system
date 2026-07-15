"""Vehicle Master enhancement - identifiers, classification, financials, insurance, assignment fields, engine_number uniqueness

Revision ID: b094aeb877a7
Revises: 9f84bf5ae91a
Create Date: 2026-07-15 12:30:10.167287

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b094aeb877a7'
down_revision = '9f84bf5ae91a'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('vehicles', schema=None) as batch_op:
        batch_op.add_column(sa.Column('far_number', sa.String(length=60), nullable=True))
        batch_op.add_column(sa.Column('cr_number', sa.String(length=60), nullable=True))
        batch_op.add_column(sa.Column('mv_file_number', sa.String(length=60), nullable=True))
        batch_op.add_column(sa.Column('remarks', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('vehicle_body_type', sa.String(length=40), nullable=True))
        batch_op.add_column(sa.Column('displacement', sa.String(length=40), nullable=True))
        batch_op.add_column(sa.Column('component_group', sa.String(length=40), nullable=True))
        batch_op.add_column(sa.Column('supplier', sa.String(length=120), nullable=True))
        batch_op.add_column(sa.Column('leasing_company', sa.String(length=120), nullable=True))
        batch_op.add_column(sa.Column('top_up_amount', sa.Numeric(precision=18, scale=2), nullable=True))
        batch_op.add_column(sa.Column('assured_value_current_year', sa.Numeric(precision=18, scale=2), nullable=True))
        batch_op.add_column(sa.Column('delivery_date', sa.Date(), nullable=True))
        batch_op.add_column(sa.Column('start_date', sa.Date(), nullable=True))
        batch_op.add_column(sa.Column('end_date', sa.Date(), nullable=True))
        batch_op.add_column(sa.Column('insurance_reference_number', sa.String(length=60), nullable=True))
        batch_op.add_column(sa.Column('comprehensive_policy_number', sa.String(length=60), nullable=True))
        batch_op.add_column(sa.Column('comprehensive_insurance_provider', sa.String(length=120), nullable=True))
        batch_op.add_column(sa.Column('ctpl_policy_number', sa.String(length=60), nullable=True))
        batch_op.add_column(sa.Column('ctpl_insurance_provider', sa.String(length=120), nullable=True))
        batch_op.add_column(sa.Column('lto_office', sa.String(length=120), nullable=True))
        batch_op.add_column(sa.Column('has_ctpl', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column('ctpl_from_date', sa.Date(), nullable=True))
        batch_op.add_column(sa.Column('ctpl_to_date', sa.Date(), nullable=True))
        batch_op.add_column(sa.Column('has_od_theft_aon', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column('od_theft_aon_from_date', sa.Date(), nullable=True))
        batch_op.add_column(sa.Column('od_theft_aon_to_date', sa.Date(), nullable=True))
        batch_op.add_column(sa.Column('has_vtpl_pd', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column('vtpl_pd_from_date', sa.Date(), nullable=True))
        batch_op.add_column(sa.Column('vtpl_pd_to_date', sa.Date(), nullable=True))
        batch_op.add_column(sa.Column('has_vtpl_bi', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column('vtpl_bi_from_date', sa.Date(), nullable=True))
        batch_op.add_column(sa.Column('vtpl_bi_to_date', sa.Date(), nullable=True))
        batch_op.add_column(sa.Column('has_inland_marine', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column('assignment', sa.String(length=10), nullable=True))
        batch_op.add_column(sa.Column('assignment_group_classification', sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column('vehicle_usage', sa.String(length=12), nullable=True))
        batch_op.add_column(sa.Column('mr_eds', sa.Boolean(), nullable=True))
        batch_op.add_column(sa.Column('with_vehicle_contract', sa.Boolean(), nullable=True))
        # NOTE: if this fails on a real database with "Duplicate entry for
        # key engine_number", it means two or more existing vehicles
        # already share the same (non-blank) Engine Number — that data
        # must be corrected by hand before this constraint can apply.
        # Blank/NULL engine numbers are unaffected (NULLs don't collide
        # under a UNIQUE constraint).
        batch_op.create_unique_constraint('uq_vehicles_engine_number',
                                          ['engine_number'])

    with op.batch_alter_table('vehicles', schema=None) as batch_op:
        batch_op.alter_column('has_ctpl', server_default=None)
        batch_op.alter_column('has_od_theft_aon', server_default=None)
        batch_op.alter_column('has_vtpl_pd', server_default=None)
        batch_op.alter_column('has_vtpl_bi', server_default=None)
        batch_op.alter_column('has_inland_marine', server_default=None)


def downgrade():
    with op.batch_alter_table('vehicles', schema=None) as batch_op:
        batch_op.drop_constraint('uq_vehicles_engine_number', type_='unique')
        batch_op.drop_column('with_vehicle_contract')
        batch_op.drop_column('mr_eds')
        batch_op.drop_column('vehicle_usage')
        batch_op.drop_column('assignment_group_classification')
        batch_op.drop_column('assignment')
        batch_op.drop_column('has_inland_marine')
        batch_op.drop_column('vtpl_bi_to_date')
        batch_op.drop_column('vtpl_bi_from_date')
        batch_op.drop_column('has_vtpl_bi')
        batch_op.drop_column('vtpl_pd_to_date')
        batch_op.drop_column('vtpl_pd_from_date')
        batch_op.drop_column('has_vtpl_pd')
        batch_op.drop_column('od_theft_aon_to_date')
        batch_op.drop_column('od_theft_aon_from_date')
        batch_op.drop_column('has_od_theft_aon')
        batch_op.drop_column('ctpl_to_date')
        batch_op.drop_column('ctpl_from_date')
        batch_op.drop_column('has_ctpl')
        batch_op.drop_column('lto_office')
        batch_op.drop_column('ctpl_insurance_provider')
        batch_op.drop_column('ctpl_policy_number')
        batch_op.drop_column('comprehensive_insurance_provider')
        batch_op.drop_column('comprehensive_policy_number')
        batch_op.drop_column('insurance_reference_number')
        batch_op.drop_column('end_date')
        batch_op.drop_column('start_date')
        batch_op.drop_column('delivery_date')
        batch_op.drop_column('assured_value_current_year')
        batch_op.drop_column('top_up_amount')
        batch_op.drop_column('leasing_company')
        batch_op.drop_column('supplier')
        batch_op.drop_column('component_group')
        batch_op.drop_column('displacement')
        batch_op.drop_column('vehicle_body_type')
        batch_op.drop_column('remarks')
        batch_op.drop_column('mv_file_number')
        batch_op.drop_column('cr_number')
        batch_op.drop_column('far_number')
