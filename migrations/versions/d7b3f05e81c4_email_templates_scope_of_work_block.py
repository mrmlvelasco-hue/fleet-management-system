"""Add the Scope of Work block to already-seeded approval email templates

Revision ID: d7b3f05e81c4
Revises: c4e1a7b92d10
Create Date: 2026-10-04 12:05:00.000000

Fresh installs get the block from `flask seed` (cli._seed_email_templates),
but seeding skips templates that already exist, so a running database
would never show an order's Scope of Work in its approval emails.

Conservative by design -- an admin may have rewritten a template, and
their wording wins:
  * only the seven approval-event templates are considered;
  * a template already mentioning scope_items is left alone (idempotent);
  * the block is inserted only right after the seeded document-number
    line (`<p><strong>{{ document_number }}</strong></p>` in HTML,
    `{{ document_number }}` + blank line in text). A template whose
    structure no longer contains that exact line is skipped untouched --
    the admin can add `{{ scope_items }}` themselves under
    System Administration -> Email Templates.

The block strings are copied here, not imported from app code, so this
migration keeps doing exactly what it did today even if the app's copy
changes later. The block renders nothing for documents without scope
lines, so it is safe in every approval template.

Downgrade removes exactly the inserted strings.
"""
from alembic import op
import sqlalchemy as sa


revision = "d7b3f05e81c4"
down_revision = "c4e1a7b92d10"
branch_labels = None
depends_on = None

EVENTS = ("submitted", "approved_level", "approved_final", "rejected",
          "returned", "resubmitted", "cancelled")

HTML_ANCHOR = "<p><strong>{{ document_number }}</strong></p>"
TEXT_ANCHOR = "{{ document_number }}\n\n"

SCOPE_BLOCK_HTML = (
    "{% if scope_items %}"
    "<p><strong>Scope of Work</strong></p><ol>"
    "{% for line in scope_items %}<li>"
    "{% if line.code %}{{ line.code|e }} &ndash; {% endif %}"
    "{{ line.description|e }}</li>{% endfor %}</ol>"
    "{% endif %}")
SCOPE_BLOCK_TEXT = (
    "{% if scope_items %}Scope of Work:\n"
    "{% for line in scope_items %}  {{ loop.index }}. "
    "{% if line.code %}{{ line.code }} - {% endif %}"
    "{{ line.description }}\n{% endfor %}\n{% endif %}")

_templates = sa.table(
    "email_templates",
    sa.column("id", sa.Integer),
    sa.column("event_code", sa.String(80)),
    sa.column("body_html", sa.Text),
    sa.column("body_text", sa.Text),
)


def _rows(conn):
    return conn.execute(
        sa.select(_templates.c.id, _templates.c.body_html,
                  _templates.c.body_text)
        .where(_templates.c.event_code.in_(EVENTS))).fetchall()


def upgrade():
    conn = op.get_bind()
    for row_id, html, text in _rows(conn):
        html = html or ""
        text = text or ""
        if "scope_items" in html or "scope_items" in text:
            continue
        new_html = (html.replace(HTML_ANCHOR, HTML_ANCHOR + SCOPE_BLOCK_HTML, 1)
                    if HTML_ANCHOR in html else html)
        new_text = (text.replace(TEXT_ANCHOR, TEXT_ANCHOR + SCOPE_BLOCK_TEXT, 1)
                    if TEXT_ANCHOR in text else text)
        if new_html != html or new_text != text:
            conn.execute(_templates.update()
                         .where(_templates.c.id == row_id)
                         .values(body_html=new_html, body_text=new_text))


def downgrade():
    conn = op.get_bind()
    for row_id, html, text in _rows(conn):
        html = html or ""
        text = text or ""
        new_html = html.replace(SCOPE_BLOCK_HTML, "")
        new_text = text.replace(SCOPE_BLOCK_TEXT, "")
        if new_html != html or new_text != text:
            conn.execute(_templates.update()
                         .where(_templates.c.id == row_id)
                         .values(body_html=new_html, body_text=new_text))
