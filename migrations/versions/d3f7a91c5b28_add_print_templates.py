"""add print_templates

Editable bodies for printed documents -- currently the Oath of
Undertaking issued with a Vehicle Assignment Memo.

Its own table rather than a row in email_templates: the Oath is a signed
legal undertaking, and filing it behind a screen labelled "Email
Templates" would make it hard to find and easy to mistake for
correspondence. The client asked for the separation explicitly.

paper_size and orientation live on the row because the right paper is a
property of the DOCUMENT: an undertaking filed on Legal in one office
and A4 in another is a real operational difference, and hardcoding it in
a stylesheet would force a code change to switch.

Revision ID: d3f7a91c5b28
Revises: c8b2d5f1a704
Create Date: 2026-08-18

"""
from alembic import op
import sqlalchemy as sa


revision = 'd3f7a91c5b28'
down_revision = 'c8b2d5f1a704'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'print_templates',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('code', sa.String(length=80), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('description', sa.String(length=255), nullable=True),
        sa.Column('body_html', sa.Text(), nullable=False),
        sa.Column('paper_size', sa.String(length=10), nullable=False,
                  server_default='A4'),
        sa.Column('orientation', sa.String(length=10), nullable=False,
                  server_default='PORTRAIT'),
        # BaseModel columns, matching every other table in the system.
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('updated_by', sa.Integer(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False,
                  server_default=sa.true()),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code', name='uq_print_templates_code'),
    )
    op.create_index('ix_print_templates_code', 'print_templates', ['code'])


def downgrade():
    op.drop_index('ix_print_templates_code', table_name='print_templates')
    op.drop_table('print_templates')
