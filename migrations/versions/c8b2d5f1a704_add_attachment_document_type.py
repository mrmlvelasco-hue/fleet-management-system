"""add attachment document type (OR / CR / etc.)

Adds attachments.document_type, holding a Lookup code from
ATTACHMENT_DOC_TYPE. Before this, an Attachment recorded only a
filename, so nothing in the system knew which file was the OR and which
was the CR -- the vehicle report could only render one undifferentiated
gallery, and the sole clue was whatever the uploader happened to name
the file.

A Lookup rather than an enum so the client can add document types
through Lookup Maintenance without a code release.

Nullable and deliberately NOT backfilled. Everything uploaded before
this column existed has no type and never will unless somebody tags it;
guessing a type from a filename would be worse than leaving it blank,
because a wrong label on a legal document is more damaging than no
label. Untyped attachments still appear in reports under a general
heading -- they are not hidden for lacking a classification nobody was
asked to supply.

Revision ID: c8b2d5f1a704
Revises: b7a1c9d4e2f6
Create Date: 2026-08-16

"""
from alembic import op
import sqlalchemy as sa


revision = 'c8b2d5f1a704'
down_revision = 'b7a1c9d4e2f6'
branch_labels = None
depends_on = None


def upgrade():
    # batch_alter_table so this works on SQLite (tests) as well as
    # MySQL (production).
    with op.batch_alter_table('attachments', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('document_type', sa.String(length=80), nullable=True))
        batch_op.create_index('ix_attachments_document_type',
                              ['document_type'], unique=False)


def downgrade():
    with op.batch_alter_table('attachments', schema=None) as batch_op:
        batch_op.drop_index('ix_attachments_document_type')
        batch_op.drop_column('document_type')
