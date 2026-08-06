"""Generic Auto Numbering Engine.

Every document number is produced from a configurable NumberingScheme:
[prefix][sep][YYYY][sep][MM][sep][NNNNNN][sep][suffix], segments included per
scheme flags.

The counter is advanced in its OWN, independently committed session --
not the caller's -- and this independence is load-bearing, not a style
choice. A number, once issued, must never be reissued even if the
document that would have used it is later abandoned: exactly how a
paper numbering system works, where a voided form still "used" its
number rather than freeing it for reuse.

This matters concretely for collision recovery. If the counter's
increment only lived inside the CALLER's transaction (flushed but not
committed), then a caller that rolls back after a failed insert --
e.g. retrying because the number it just tried collided with an
already-committed row -- would ALSO undo the counter's own advance.
The very next attempt would read the counter's pre-increment value and
regenerate the IDENTICAL number that just failed, forever. Confirmed
directly: two generate() calls with a rollback in between returned the
same number both times before this fix, which is exactly why an
earlier attempt to retry a numbering collision at the call site never
actually recovered -- the retry was retrying against a counter that
never moved.

Row-locked (SELECT ... FOR UPDATE) within that independent transaction
so concurrent generations still never collide with each other.
(SQLite ignores FOR UPDATE but is single-writer anyway; MySQL/MSSQL
enforce it.)
"""
from datetime import datetime, timezone

from sqlalchemy.orm import sessionmaker

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
        """Generate the next number for the document type.

        Fully self-contained: advances and commits the counter itself
        in an independent session, so the returned number is
        permanently consumed the moment this call returns -- it does
        NOT depend on the caller committing anything, and a later
        rollback in the caller's own transaction cannot undo it.
        """
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

        next_number = self._advance_counter(scheme.id, scope_year, scope_month)
        return format_number(scheme, next_number, year, month)

    @staticmethod
    def _advance_counter(scheme_id: int, scope_year: int, scope_month: int) -> int:
        """Increment the counter in a session of its own and commit
        immediately, so the advance survives no matter what the caller
        does with its own transaction afterward.

        A fresh sessionmaker bound to the SAME engine, not db.session:
        a genuinely separate transaction is exactly what makes this
        durable independent of the caller, which is the entire point.
        """
        Session = sessionmaker(bind=db.engine)
        session = Session()
        try:
            counter = (session.query(NumberingCounter)
                      .filter_by(scheme_id=scheme_id, year=scope_year,
                               month=scope_month)
                      .with_for_update()
                      .first())
            if counter is None:
                counter = NumberingCounter(
                    scheme_id=scheme_id, year=scope_year, month=scope_month,
                    last_number=0)
                session.add(counter)
                session.flush()
            counter.last_number += 1
            value = counter.last_number
            session.commit()
            return value
        finally:
            session.close()
