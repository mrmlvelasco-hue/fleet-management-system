"""add fuel transaction needs_review column

Adds a derived, indexed boolean column to fuel_transactions so the
Exceptions/flagged view and its badge count can filter on one indexed
column instead of an OR across anomaly_flags and odometer_status,
which neither column's own index can serve (confirmed via EXPLAIN
QUERY PLAN against a 1,000,000-row synthetic dataset during the
2026-11-08 Fuel Management performance audit: that OR falls back to a
full table scan on every call, growing as fuel history accumulates).

The column is kept in sync going forward by a SQLAlchemy ORM event on
FuelTransaction (before_insert / before_update) -- never set directly
by callers. This migration adds the column and backfills EXISTING
rows from their current anomaly_flags / odometer_status, so nothing
already flagged silently drops out of the Exceptions view the moment
this ships.

Revision ID: 5d31e3996290
Revises: a8e2f615c9d3
Create Date: 2026-11-08 22:20:30.061427

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '5d31e3996290'
down_revision = 'a8e2f615c9d3'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('fuel_transactions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('needs_review', sa.Boolean(),
                                      nullable=False,
                                      server_default=sa.false()))
        batch_op.create_index(
            batch_op.f('ix_fuel_transactions_needs_review'),
            ['needs_review'], unique=False)

    # Backfill existing rows from their current anomaly_flags /
    # odometer_status, so nothing already flagged drops out of the
    # Exceptions view the moment this ships.
    conn = op.get_bind()
    conn.execute(sa.text("""
        UPDATE fuel_transactions
        SET needs_review = 1
        WHERE anomaly_flags IS NOT NULL
           OR odometer_status IN ('SUSPECT', 'MISSING')
    """))

    # Drop the server_default once existing rows are backfilled -- it
    # was only needed to satisfy the NOT NULL constraint during the
    # ALTER itself; new rows rely on the model's own default / the
    # before_insert event.
    with op.batch_alter_table('fuel_transactions', schema=None) as batch_op:
        batch_op.alter_column('needs_review', server_default=None)


def downgrade():
    with op.batch_alter_table('fuel_transactions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_fuel_transactions_needs_review'))
        batch_op.drop_column('needs_review')
