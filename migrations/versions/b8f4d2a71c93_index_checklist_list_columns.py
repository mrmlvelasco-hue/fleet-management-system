"""index the columns the checklist list filters on

Measured at the client's scale -- 5,000 vehicles with a daily checklist,
so ~1.8M rows a year -- the list filters on inspection_date, status,
branch_id and created_by, none of which were indexed. On MySQL that is a
full scan per page; SQLite at 300k rows does not reveal it.

created_by is indexed too because it carries the MANDATORY driver scope
(a driver sees only their own), so it is on the hot path of every
request from the field app.

Revision ID: b8f4d2a71c93
Revises: a7e3f9c14b52
"""
from alembic import op
import sqlalchemy as sa

revision = "b8f4d2a71c93"
down_revision = "a7e3f9c14b52"
branch_labels = None
depends_on = None

TABLE = "vehicle_checklists"
INDEXES = [
    ("ix_vehicle_checklists_inspection_date", ["inspection_date"]),
    ("ix_vehicle_checklists_status", ["status"]),
    ("ix_vehicle_checklists_branch_id", ["branch_id"]),
    ("ix_vehicle_checklists_created_by", ["created_by"]),
    # Composite for the field app's own query: "my inspections, newest
    # first". A driver's list filters created_by and orders by date, and
    # two separate indexes cannot serve that as well as one that matches
    # the shape of the query.
    ("ix_vehicle_checklists_created_by_date", ["created_by", "inspection_date"]),
]


def upgrade():
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if TABLE not in set(insp.get_table_names()):
        return
    existing = {i["name"] for i in insp.get_indexes(TABLE)}
    for name, cols in INDEXES:
        if name not in existing:
            op.create_index(name, TABLE, cols)


def downgrade():
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if TABLE not in set(insp.get_table_names()):
        return
    existing = {i["name"] for i in insp.get_indexes(TABLE)}
    for name, _cols in INDEXES:
        if name in existing:
            op.drop_index(name, table_name=TABLE)
