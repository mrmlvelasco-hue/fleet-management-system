"""Add type-wide ATD authorization columns to authority_to_drives

Revision ID: f3a7c9e1b4d2
Revises: d2f6a9c13e58
Create Date: 2026-11-05 22:00:00.000000

The model (app/modules/transactions/atd/models.py) picked up three new
columns and a relaxed nullable constraint during the type-wide ATD
work -- an ATD can now authorize a vehicle TYPE or category (e.g. "any
motorcycle") instead of always binding a single fleet unit -- but no
migration was ever committed alongside that model change. Any install
that ran `flask db upgrade` after that change still has the old
column-for-column schema and 500s on the very first ATD list query
(Michael, 2026-11-05: "Unknown column
'authority_to_drives.authorization_type' in 'field list'" on
production). This migration is that missing step, applied retroactively.

- vehicle_id becomes nullable: a type-wide ATD has no single vehicle.
- authorization_type: SPECIFIC_VEHICLE | VEHICLE_TYPE | CATEGORY (not
  enforced at the DB level, same as every other status/type column in
  this codebase). Existing rows get the SPECIFIC_VEHICLE default via
  server_default, so they keep meaning exactly what they always meant
  without a manual backfill.
- vehicle_type_id: FK to vehicle_types, set only for a type-specific
  ATD.
- vehicle_category: free lookup code (LIGHT/HEAVY/MOTORCYCLE/SPECIAL)
  for a category-wide ATD; not an FK since VEHICLE_CATEGORY is a
  Lookup-table code, not its own table.
"""
from alembic import op
import sqlalchemy as sa


revision = "f3a7c9e1b4d2"
down_revision = "d2f6a9c13e58"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("authority_to_drives", schema=None) as batch_op:
        batch_op.alter_column("vehicle_id", existing_type=sa.Integer(),
                              nullable=True)
        batch_op.add_column(sa.Column("authorization_type",
                                      sa.String(length=24), nullable=False,
                                      server_default="SPECIFIC_VEHICLE"))
        batch_op.add_column(sa.Column("vehicle_type_id", sa.Integer(),
                                      nullable=True))
        batch_op.add_column(sa.Column("vehicle_category",
                                      sa.String(length=80), nullable=True))
        batch_op.create_foreign_key(
            "fk_authority_to_drives_vehicle_type_id", "vehicle_types",
            ["vehicle_type_id"], ["id"])


def downgrade():
    # NOTE: any row created with vehicle_id NULL (a genuine type-wide or
    # category-wide ATD) cannot be represented once vehicle_id is made
    # NOT NULL again -- this downgrade will fail on such a row, which is
    # the correct behaviour (there is no vehicle to fall back to).
    with op.batch_alter_table("authority_to_drives", schema=None) as batch_op:
        batch_op.drop_constraint("fk_authority_to_drives_vehicle_type_id",
                                 type_="foreignkey")
        batch_op.drop_column("vehicle_category")
        batch_op.drop_column("vehicle_type_id")
        batch_op.drop_column("authorization_type")
        batch_op.alter_column("vehicle_id", existing_type=sa.Integer(),
                              nullable=False)
