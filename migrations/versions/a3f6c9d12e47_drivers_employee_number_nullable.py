"""Make drivers.employee_number nullable

Companion to the REQUIRE_EMPLOYEE_NUMBER System Parameter
(DriverService.create, import_service.py): a legacy roster often has no
HR employee number on file for some assignees, and the column was
NOT NULL + UNIQUE at the DB level -- so even with the parameter
switched off, the database itself still rejected the row. Same shape as
license_number, which is nullable + unique for the same reason.

MySQL/MariaDB allow more than one NULL in a UNIQUE index (NULL is never
equal to NULL), so this does not weaken the uniqueness guarantee for
every row that DOES have an employee number.

Revision ID: a3f6c9d12e47
Revises: 77cfc37cfe4a
Create Date: 2026-11-14 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a3f6c9d12e47'
down_revision = '77cfc37cfe4a'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('drivers', schema=None) as batch_op:
        batch_op.alter_column(
            'employee_number',
            existing_type=sa.String(length=30),
            nullable=True)


def downgrade():
    with op.batch_alter_table('drivers', schema=None) as batch_op:
        batch_op.alter_column(
            'employee_number',
            existing_type=sa.String(length=30),
            nullable=False)
