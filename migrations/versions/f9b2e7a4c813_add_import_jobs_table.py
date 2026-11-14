"""Add import_jobs table for async bulk-import progress tracking

Revision ID: f9b2e7a4c813
Revises: eddcc3890270
Create Date: 2026-11-14 00:00:00.000000

One generic table shared by the Vehicle/Driver/Vendor Excel importers so
the frontend can poll a real row-by-row percentage while the actual
import runs on a background thread, instead of blocking the whole
upload request until every row is processed.
"""
from alembic import op
import sqlalchemy as sa

revision = 'f9b2e7a4c813'
down_revision = 'eddcc3890270'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'import_jobs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('job_type', sa.String(length=20), nullable=False),
        sa.Column('status', sa.String(length=10), nullable=False,
                   server_default='queued'),
        sa.Column('total_rows', sa.Integer(), nullable=False,
                   server_default='0'),
        sa.Column('processed_rows', sa.Integer(), nullable=False,
                   server_default='0'),
        sa.Column('created_count', sa.Integer(), nullable=True),
        sa.Column('skipped_count', sa.Integer(), nullable=True),
        sa.Column('errors', sa.JSON(), nullable=True),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_import_jobs_job_type', 'import_jobs', ['job_type'])


def downgrade():
    op.drop_index('ix_import_jobs_job_type', table_name='import_jobs')
    op.drop_table('import_jobs')
