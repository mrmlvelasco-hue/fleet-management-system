"""TransactionType.default_scope_template_id FK

Revision ID: f1b4e7c2a903
Revises: e9f2a1c3b7d5
Create Date: 2026-10-05 14:00:00.000000

Adds an optional FK from mo_transaction_types to pm_scope_templates.
When set, new MOs created with that transaction type pre-fill the Scope
of Work from that template (unless the user picks a different one in
the form, or the form pre-fill takes precedence).

Nullable by design: NULL means "no default -- the user chooses or
leaves it empty", matching every existing transaction type.
No cascade delete: deactivating a template clears the FK via a service
call rather than silently cascading.
batch_alter_table for SQLite / MySQL / SQL Server portability.
"""
from alembic import op
import sqlalchemy as sa


revision = "f1b4e7c2a903"
down_revision = "e9f2a1c3b7d5"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("mo_transaction_types", schema=None) as batch_op:
        batch_op.add_column(sa.Column(
            "default_scope_template_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_mott_default_scope_template",
            "pm_scope_templates",
            ["default_scope_template_id"], ["id"],
            ondelete="SET NULL")


def downgrade():
    with op.batch_alter_table("mo_transaction_types", schema=None) as batch_op:
        batch_op.drop_constraint(
            "fk_mott_default_scope_template", type_="foreignkey")
        batch_op.drop_column("default_scope_template_id")
