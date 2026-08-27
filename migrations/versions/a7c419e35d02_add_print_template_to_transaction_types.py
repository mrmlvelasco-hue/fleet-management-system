"""Add print_template to mo_transaction_types.

Which printed document each transaction type produces, held as DATA
rather than as an if/elif on transaction code. The reason is the same
one already recorded for maintenance_class on this table: encoding it
in code means every new transaction type a client adds in System
Administration needs a developer before it can print, which contradicts
the system's own rule that business configuration lives in System
Administration.

The backfill below sets the mapping supplied by the client. One note
on how it differs from the SQL as supplied:

  * The disposal rule is applied by GROUP only. The supplied SQL used
    `code LIKE 'DIS-%' OR "group" = 'DISPOSAL'`; the group check alone
    covers every seeded disposal code and also catches any disposal
    type a client adds later under a different code prefix, which the
    LIKE would miss.

Revision ID: a7c419e35d02
Revises: d3f7a91c5b28
"""
from alembic import op
import sqlalchemy as sa

revision = "a7c419e35d02"
down_revision = "d3f7a91c5b28"
branch_labels = None
depends_on = None


#: code (or group) -> template. Kept here as the authoritative backfill;
#: after this migration it is editable data, not code.
_BY_CODE = {
    "DEP-ASSIGNMENT": "vehicle_assignment_memo",
    "DEP-REASSIGNMENT": "vehicle_reassignment_memo",
    "DEP-RELOCATION": "vehicle_relocation_memo",
    "DEP-TRANSFER": "maintenanceorder_print_transfer",
    "MAINT-SERVICING": "pm_work_order",
    "MAINT-REPAIR": "repair_work_order",
}


def upgrade():
    op.add_column(
        "mo_transaction_types",
        sa.Column("print_template", sa.String(length=60), nullable=True),
    )

    conn = op.get_bind()
    for code, template in _BY_CODE.items():
        conn.execute(
            sa.text("UPDATE mo_transaction_types SET print_template = :t "
                    "WHERE code = :c"),
            {"t": template, "c": code},
        )

    # Every disposal type, by group -- see the module docstring for why
    # this is not a code prefix match.
    conn.execute(
        sa.text('UPDATE mo_transaction_types SET print_template = :t '
                'WHERE "group" = :g'),
        {"t": "maintenanceorder_print_disposal", "g": "DISPOSAL"},
    )

    # Everything else is left NULL rather than written to
    # 'maintenanceorder_print'. The resolver already treats NULL as the
    # generic work order, and writing the default into every row would
    # make a deliberately-generic type indistinguishable from one nobody
    # has configured yet.


def downgrade():
    op.drop_column("mo_transaction_types", "print_template")
