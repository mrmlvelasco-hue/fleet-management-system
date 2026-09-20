"""add fleet broadcast tables

Revision ID: cb7c76fc102c
Revises: d2f6a8c14e97
Create Date: 2026-09-15 15:40:47.823257

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = 'cb7c76fc102c'
down_revision = 'd2f6a8c14e97'
branch_labels = None
depends_on = None


def upgrade():
    # Fleet Broadcast tables only.
    op.create_table(
        'fleet_broadcasts',
        sa.Column('broadcast_no', sa.String(length=40), nullable=False),
        sa.Column('broadcast_type', sa.String(length=30), nullable=False),
        sa.Column('category', sa.String(length=50), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('priority', sa.String(length=20), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('effective_date', sa.DateTime(), nullable=True),
        sa.Column('expiry_date', sa.DateTime(), nullable=True),
        sa.Column('created_by', sa.Integer(), nullable=False),
        sa.Column('published_by', sa.Integer(), nullable=True),
        sa.Column('published_at', sa.DateTime(), nullable=True),
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('updated_by', sa.Integer(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(['created_by'], ['users.id']),
        sa.ForeignKeyConstraint(['published_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id')
    )

    with op.batch_alter_table('fleet_broadcasts', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_fleet_broadcasts_broadcast_no'),
            ['broadcast_no'],
            unique=True
        )
        batch_op.create_index(
            batch_op.f('ix_fleet_broadcasts_category'),
            ['category'],
            unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_fleet_broadcasts_status'),
            ['status'],
            unique=False
        )

    op.create_table(
        'fleet_broadcast_acknowledgements',
        sa.Column('broadcast_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('acknowledged_at', sa.DateTime(), nullable=False),
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('updated_by', sa.Integer(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ['broadcast_id'],
            ['fleet_broadcasts.id'],
            ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'broadcast_id',
            'user_id',
            name='uq_fleet_broadcast_ack_user'
        )
    )

    with op.batch_alter_table(
        'fleet_broadcast_acknowledgements',
        schema=None
    ) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_fleet_broadcast_acknowledgements_broadcast_id'),
            ['broadcast_id'],
            unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_fleet_broadcast_acknowledgements_user_id'),
            ['user_id'],
            unique=False
        )

    op.create_table(
        'fleet_broadcast_recipients',
        sa.Column('broadcast_id', sa.Integer(), nullable=False),
        sa.Column('recipient_type', sa.String(length=30), nullable=False),
        sa.Column('role_id', sa.Integer(), nullable=True),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('branch_id', sa.Integer(), nullable=True),
        sa.Column('department_id', sa.Integer(), nullable=True),
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('updated_by', sa.Integer(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ['broadcast_id'],
            ['fleet_broadcasts.id'],
            ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(['role_id'], ['roles.id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id')
    )

    with op.batch_alter_table(
        'fleet_broadcast_recipients',
        schema=None
    ) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_fleet_broadcast_recipients_branch_id'),
            ['branch_id'],
            unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_fleet_broadcast_recipients_broadcast_id'),
            ['broadcast_id'],
            unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_fleet_broadcast_recipients_department_id'),
            ['department_id'],
            unique=False
        )


def downgrade():
    # Remove Fleet Broadcast tables only.
    with op.batch_alter_table(
        'fleet_broadcast_recipients',
        schema=None
    ) as batch_op:
        batch_op.drop_index(
            batch_op.f('ix_fleet_broadcast_recipients_department_id')
        )
        batch_op.drop_index(
            batch_op.f('ix_fleet_broadcast_recipients_broadcast_id')
        )
        batch_op.drop_index(
            batch_op.f('ix_fleet_broadcast_recipients_branch_id')
        )

    op.drop_table('fleet_broadcast_recipients')

    with op.batch_alter_table(
        'fleet_broadcast_acknowledgements',
        schema=None
    ) as batch_op:
        batch_op.drop_index(
            batch_op.f('ix_fleet_broadcast_acknowledgements_user_id')
        )
        batch_op.drop_index(
            batch_op.f('ix_fleet_broadcast_acknowledgements_broadcast_id')
        )

    op.drop_table('fleet_broadcast_acknowledgements')

    with op.batch_alter_table(
        'fleet_broadcasts',
        schema=None
    ) as batch_op:
        batch_op.drop_index(
            batch_op.f('ix_fleet_broadcasts_status')
        )
        batch_op.drop_index(
            batch_op.f('ix_fleet_broadcasts_category')
        )
        batch_op.drop_index(
            batch_op.f('ix_fleet_broadcasts_broadcast_no')
        )

    op.drop_table('fleet_broadcasts')
