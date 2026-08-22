"""Coercing JSON payload values to their column types.

Lifted out of `api/vehicles.py`, where it was written for the Vehicle
write endpoints, before a second module needs it. Drivers has
`license_expiry`; without this it would reproduce the fault vehicles
just had:

    TypeError: '>=' not supported between instances of 'str' and 'int'

JSON has no separate integer input, and a browser form holds every value
as a string. SQLAlchemy coerces on FLUSH, so the row is written
correctly and a later GET reads back the right type -- but the
in-memory object still holds the string, and anything that computes
against that object before it is refreshed sees a string. So a write
SUCCEEDS and then fails while reporting its own result, which reads to
the user as a failed save for a change that was in fact committed.

The Jinja routes have always done this, field by field, inline
(`int(f["branch_id"])`, `parse_form_date(...)`). This is the same
conversion, declared per module instead of repeated per field, so an API
path and its Jinja counterpart cannot build differently-typed objects
from identical input.

Dates go through `parse_form_date`, which is shared with the Jinja
routes: it already owns the wording of a bad-date message, and a second
parser would be a second opinion on what counts as a valid date.
"""
from datetime import date
from decimal import Decimal, InvalidOperation


class FieldValueError(Exception):
    """A value that could not be coerced, carrying which field it was.

    The underlying parsers raise one exception TYPE for all fields of a
    kind, with the field's label inside the message. Callers attribute
    errors by type, so without this every bad date would land at form
    level -- "check the dates" against a form holding fourteen of them.

    Coercing without shaping the failure would also just swap one 500
    for another: int("abc") raises ValueError, which nothing catches.
    """

    def __init__(self, message, field):
        super().__init__(message)
        self.field = field


class Coercer:
    """Per-module declaration of which writable fields are which type.

    Labels are the ones the Jinja form uses, so both apps report the
    same problem in the same words.
    """

    def __init__(self, *, ints=None, decimals=None, dates=None):
        self.ints = ints or {}
        self.decimals = decimals or {}
        self.dates = dates or {}

    def apply(self, key, value):
        """Coerce one value. Returns it unchanged if it needs nothing."""
        if not isinstance(value, str):
            return value
        if key in self.ints:
            return self._int(key, value)
        if key in self.decimals:
            return self._decimal(key, value)
        if key in self.dates:
            return self._date(key, value)
        return value

    def fields(self, payload, allowed):
        """Allow-listed fields from `payload`, coerced."""
        return {k: self.apply(k, v)
                for k, v in payload.items() if k in allowed}

    # ── per type ────────────────────────────────────────────────────

    def _int(self, key, value):
        # "" means the field was cleared, not zero. int("") raises, and
        # defaulting to 0 would record a vehicle as having covered no
        # kilometres rather than as unmeasured -- a claim, not a gap.
        if not value.strip():
            return None
        try:
            return int(value.strip())
        except ValueError:
            raise FieldValueError(
                f"{self.ints[key]} must be a whole number.", key) from None

    def _decimal(self, key, value):
        if not value.strip():
            return None
        try:
            # Decimal, never float: float("0.1") + float("0.2") is not
            # 0.3, and a cost that drifts by a centavo is a figure the
            # client will eventually reconcile against something else.
            #
            # Separators stripped, since the form displays amounts
            # grouped and a pasted-back "1,234,567.89" is a value the
            # user believes they entered correctly.
            return Decimal(value.strip().replace(",", ""))
        except InvalidOperation:
            raise FieldValueError(
                f"{self.decimals[key]} must be an amount.", key) from None

    def _date(self, key, value):
        from app.core.validation.date_utils import (
            DateFormatError, parse_form_date)
        try:
            return parse_form_date(value, self.dates[key])
        except DateFormatError as exc:
            raise FieldValueError(str(exc), key) from exc


__all__ = ["Coercer", "FieldValueError", "date", "Decimal"]
