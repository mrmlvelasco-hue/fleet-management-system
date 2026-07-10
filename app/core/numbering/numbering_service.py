"""Generic Auto Numbering Engine.

Every document number is produced from a configurable NumberingScheme:
[prefix][sep][YYYY][sep][MM][sep][NNNNNN][sep][suffix], segments included per
scheme flags. Counters are scoped by the reset policy and incremented under a
row lock (SELECT ... FOR UPDATE) so concurrent generations never collide.
(SQLite ignores FOR UPDATE but is single-writer anyway; MySQL/MSSQL enforce it.)
"""
from datetime import datetime, timezone

from app.extensions import db
from app.modules.document_config.models import NumberingCounter
from app.modules.document_config.repository import (
    DocumentTypeRepository, NumberingSchemeRepository)


class NoSchemeError(Exception):
    """Raised when the document type has no active numbering scheme."""


def format_number(scheme, number: int, year: int, month: int) -> str:
    """Assemble the formatted document number from scheme configuration."""
    parts = []
    if scheme.prefix:
        parts.append(scheme.prefix)
    if scheme.include_year:
        parts.append(f"{year:04d}")
    if scheme.include_month:
        parts.append(f"{month:02d}")
    parts.append(f"{number:0{scheme.digit_count}d}")
    if scheme.suffix:
        parts.append(scheme.suffix)
    return scheme.separator.join(parts)


class AutoNumberingService:
    def __init__(self):
        self.doc_types = DocumentTypeRepository()
        self.schemes = NumberingSchemeRepository()

    def _now(self):
        """(year, month) — separated for testability."""
        now = datetime.now(timezone.utc)
        return now.year, now.month

    def generate(self, document_type_code: str) -> str:
        """Generate the next number for the document type. Flushes; caller commits."""
        dt = self.doc_types.get_by_code(document_type_code)
        scheme = (self.schemes.get_for_document_type(dt.id)
                  if dt is not None else None)
        if scheme is None:
            raise NoSchemeError(
                f"No active numbering scheme for document type "
                f"'{document_type_code}'.")

        year, month = self._now()
        scope_year, scope_month = 0, 0
        if scheme.reset_policy == "YEARLY":
            scope_year = year
        elif scheme.reset_policy == "MONTHLY":
            scope_year, scope_month = year, month

        counter = (db.session.query(NumberingCounter)
                   .filter_by(scheme_id=scheme.id, year=scope_year,
                              month=scope_month)
                   .with_for_update()
                   .first())
        if counter is None:
            counter = NumberingCounter(scheme_id=scheme.id, year=scope_year,
                                       month=scope_month, last_number=0)
            db.session.add(counter)
            db.session.flush()

        counter.last_number += 1
        db.session.flush()
        return format_number(scheme, counter.last_number, year, month)
