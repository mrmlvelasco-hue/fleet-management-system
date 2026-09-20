"""seed the FB (Fleet Broadcast) document type and numbering scheme

Revision ID: a1f9c3d8e6b2
Revises: cb7c76fc102c
Create Date: 2026-09-19 12:00:00.000000

Fleet Broadcasts are numbered by the generic Auto Numbering Engine, the
same as every other document (TT/MO/PR/ATD), rather than typed by hand.
That requires a `document_types` row with code "FB" and a matching
`numbering_schemes` row -- without this, the first call to
FleetBroadcastService._next_broadcast_no() raises "No active numbering
scheme for document type 'FB'".

A pure data migration: `document_types` and `numbering_schemes` already
exist (Phase 1b, System Administration), so nothing here creates a table.
Written with SQLAlchemy Core rather than raw SQL, for the same portability
reason as the fleet_broadcasts migration -- MySQL and SQL Server quote
identifiers differently, and CURRENT_TIMESTAMP works on both where NOW()
does not.

Idempotent: an operator who has already run `flask seed` (which seeds the
same row for fresh installs) must not end up with two FB series, and
`flask db upgrade` run twice must not either.
"""
from alembic import op
import sqlalchemy as sa


revision = "a1f9c3d8e6b2"
down_revision = "cb7c76fc102c"
branch_labels = None
depends_on = None


_DOCUMENT_TYPES = sa.table(
    "document_types",
    sa.column("id", sa.Integer),
    sa.column("code", sa.String),
    sa.column("name", sa.String),
    sa.column("description", sa.String),
    sa.column("requires_approval", sa.Boolean),
    sa.column("auto_numbering", sa.Boolean),
    sa.column("printable", sa.Boolean),
    sa.column("mobile_available", sa.Boolean),
    sa.column("attachment_allowed", sa.Boolean),
    sa.column("created_at", sa.DateTime),
    sa.column("updated_at", sa.DateTime),
    sa.column("is_active", sa.Boolean),
)

_NUMBERING_SCHEMES = sa.table(
    "numbering_schemes",
    sa.column("document_type_id", sa.Integer),
    sa.column("prefix", sa.String),
    sa.column("suffix", sa.String),
    sa.column("include_year", sa.Boolean),
    sa.column("include_month", sa.Boolean),
    sa.column("digit_count", sa.Integer),
    sa.column("separator", sa.String),
    sa.column("reset_policy", sa.String),
    sa.column("created_at", sa.DateTime),
    sa.column("updated_at", sa.DateTime),
    sa.column("is_active", sa.Boolean),
)


def _seed(conn):
    now = sa.func.current_timestamp()

    row = conn.execute(
        sa.select(_DOCUMENT_TYPES.c.id)
        .where(_DOCUMENT_TYPES.c.code == "FB")
    ).fetchone()

    if row is None:
        conn.execute(_DOCUMENT_TYPES.insert().values(
            code="FB",
            name="Fleet Broadcast",
            description="Fleet-wide announcement, advisory or policy "
                        "notice delivered to a configured audience.",
            requires_approval=False,
            auto_numbering=True,
            printable=True,
            mobile_available=True,
            attachment_allowed=False,
            created_at=now,
            updated_at=now,
            is_active=True,
        ))
        row = conn.execute(
            sa.select(_DOCUMENT_TYPES.c.id)
            .where(_DOCUMENT_TYPES.c.code == "FB")
        ).fetchone()

    if row is None:
        return

    document_type_id = row[0]

    already = conn.execute(
        sa.select(_NUMBERING_SCHEMES.c.document_type_id)
        .where(_NUMBERING_SCHEMES.c.document_type_id == document_type_id)
    ).fetchone()

    if already:
        return

    conn.execute(_NUMBERING_SCHEMES.insert().values(
        document_type_id=document_type_id,
        prefix="FB",
        suffix="",
        include_year=True,
        include_month=False,
        digit_count=4,
        separator="-",
        reset_policy="YEARLY",
        created_at=now,
        updated_at=now,
        is_active=True,
    ))


def upgrade():
    _seed(op.get_bind())


def downgrade():
    conn = op.get_bind()
    row = conn.execute(
        sa.select(_DOCUMENT_TYPES.c.id)
        .where(_DOCUMENT_TYPES.c.code == "FB")
    ).fetchone()
    if row is None:
        return
    document_type_id = row[0]
    conn.execute(
        _NUMBERING_SCHEMES.delete()
        .where(_NUMBERING_SCHEMES.c.document_type_id == document_type_id)
    )
    conn.execute(
        _DOCUMENT_TYPES.delete()
        .where(_DOCUMENT_TYPES.c.id == document_type_id)
    )
