"""data migration: translate legacy PREVENTIVE/CORRECTIVE/PREDICTIVE category values to new PM/CM/PD lookup codes

Revision ID: 8e1501994d00
Revises: 9251a86c27dd
Create Date: 2026-07-16 13:47:56.986789

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '8e1501994d00'
down_revision = '9251a86c27dd'
branch_labels = None
depends_on = None


def upgrade():
    # Maintenance Category moved from a hardcoded PREVENTIVE/CORRECTIVE/
    # PREDICTIVE dropdown to a configurable Lookup (MAINTENANCE_CATEGORY:
    # PM/CM/INSP/AR/RC/PD). Translate any existing rows using the old
    # values so they still match a real Lookup entry going forward — the
    # application code keeps a "PREVENTIVE" fallback for safety, but
    # having live data actually on the new codes is the correct end
    # state, not a permanent crutch.
    conn = op.get_bind()
    conn.execute(sa.text(
        "UPDATE maintenance_types SET category = 'PM' WHERE category = 'PREVENTIVE'"))
    conn.execute(sa.text(
        "UPDATE maintenance_types SET category = 'CM' WHERE category = 'CORRECTIVE'"))
    conn.execute(sa.text(
        "UPDATE maintenance_types SET category = 'PD' WHERE category = 'PREDICTIVE'"))
    conn.execute(sa.text(
        "UPDATE maintenance_orders SET category = 'PM' WHERE category = 'PREVENTIVE'"))
    conn.execute(sa.text(
        "UPDATE maintenance_orders SET category = 'CM' WHERE category = 'CORRECTIVE'"))
    conn.execute(sa.text(
        "UPDATE maintenance_orders SET category = 'PD' WHERE category = 'PREDICTIVE'"))


def downgrade():
    conn = op.get_bind()
    conn.execute(sa.text(
        "UPDATE maintenance_types SET category = 'PREVENTIVE' WHERE category = 'PM'"))
    conn.execute(sa.text(
        "UPDATE maintenance_types SET category = 'CORRECTIVE' WHERE category = 'CM'"))
    conn.execute(sa.text(
        "UPDATE maintenance_types SET category = 'PREDICTIVE' WHERE category = 'PD'"))
    conn.execute(sa.text(
        "UPDATE maintenance_orders SET category = 'PREVENTIVE' WHERE category = 'PM'"))
    conn.execute(sa.text(
        "UPDATE maintenance_orders SET category = 'CORRECTIVE' WHERE category = 'CM'"))
    conn.execute(sa.text(
        "UPDATE maintenance_orders SET category = 'PREDICTIVE' WHERE category = 'PD'"))
