"""Add vehicle location tables for the mobile Rescue Map

Revision ID: a7c3e9f1d204
Revises: f9b2e7a4c813
Create Date: 2026-11-17 00:00:00.000000

Three new tables, nothing existing is altered:

  vehicle_tracking_sessions  one row per run of automatic tracking
  vehicle_location_logs      every reported point (append-only, purged
                             after LOCATION_HISTORY_RETENTION_DAYS)
  vehicle_last_locations     pointer to each vehicle's latest point, so
                             the map costs one row per vehicle however
                             large the history grows
"""
from alembic import op
import sqlalchemy as sa

revision = 'a7c3e9f1d204'
down_revision = 'f9b2e7a4c813'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'vehicle_tracking_sessions',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('client_uuid', sa.String(length=36), nullable=False),
        sa.Column('vehicle_id', sa.Integer(),
                  sa.ForeignKey('vehicles.id'), nullable=False),
        sa.Column('user_id', sa.Integer(),
                  sa.ForeignKey('users.id'), nullable=False),
        sa.Column('status', sa.String(length=10), nullable=False,
                  server_default='ACTIVE'),
        sa.Column('started_at', sa.DateTime(), nullable=False),
        sa.Column('stopped_at', sa.DateTime(), nullable=True),
        sa.Column('stop_reason', sa.String(length=30), nullable=True),
        sa.Column('last_update_at', sa.DateTime(), nullable=True),
        sa.Column('device_id', sa.String(length=64), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('client_uuid',
                            name='uq_vehicle_tracking_sessions_client_uuid'),
    )
    op.create_index('ix_vehicle_tracking_sessions_vehicle_status',
                    'vehicle_tracking_sessions', ['vehicle_id', 'status'])

    op.create_table(
        'vehicle_location_logs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('client_uuid', sa.String(length=36), nullable=False),
        sa.Column('vehicle_id', sa.Integer(),
                  sa.ForeignKey('vehicles.id'), nullable=False),
        sa.Column('latitude', sa.Numeric(9, 6), nullable=False),
        sa.Column('longitude', sa.Numeric(9, 6), nullable=False),
        sa.Column('accuracy_m', sa.Numeric(8, 2), nullable=False),
        sa.Column('altitude_m', sa.Numeric(8, 2), nullable=True),
        sa.Column('speed_mps', sa.Numeric(7, 2), nullable=True),
        sa.Column('heading_deg', sa.Numeric(5, 2), nullable=True),
        sa.Column('is_mocked', sa.Boolean(), nullable=False,
                  server_default=sa.false()),
        sa.Column('source', sa.String(length=10), nullable=False),
        sa.Column('captured_at', sa.DateTime(), nullable=False),
        sa.Column('received_at', sa.DateTime(), nullable=False),
        sa.Column('captured_by', sa.Integer(),
                  sa.ForeignKey('users.id'), nullable=False),
        sa.Column('tracking_session_id', sa.Integer(),
                  sa.ForeignKey('vehicle_tracking_sessions.id'),
                  nullable=True),
        sa.Column('device_id', sa.String(length=64), nullable=True),
        sa.UniqueConstraint('client_uuid',
                            name='uq_vehicle_location_logs_client_uuid'),
    )
    op.create_index('ix_vehicle_location_logs_vehicle_captured',
                    'vehicle_location_logs', ['vehicle_id', 'captured_at'])
    op.create_index('ix_vehicle_location_logs_reporter_captured',
                    'vehicle_location_logs', ['captured_by', 'captured_at'])
    op.create_index('ix_vehicle_location_logs_session',
                    'vehicle_location_logs', ['tracking_session_id'])

    op.create_table(
        'vehicle_last_locations',
        sa.Column('vehicle_id', sa.Integer(),
                  sa.ForeignKey('vehicles.id'), primary_key=True),
        sa.Column('log_id', sa.Integer(),
                  sa.ForeignKey('vehicle_location_logs.id'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
    )


def downgrade():
    op.drop_table('vehicle_last_locations')
    op.drop_index('ix_vehicle_location_logs_session',
                  table_name='vehicle_location_logs')
    op.drop_index('ix_vehicle_location_logs_reporter_captured',
                  table_name='vehicle_location_logs')
    op.drop_index('ix_vehicle_location_logs_vehicle_captured',
                  table_name='vehicle_location_logs')
    op.drop_table('vehicle_location_logs')
    op.drop_index('ix_vehicle_tracking_sessions_vehicle_status',
                  table_name='vehicle_tracking_sessions')
    op.drop_table('vehicle_tracking_sessions')
