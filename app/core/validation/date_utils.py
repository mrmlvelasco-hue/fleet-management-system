"""Shared form-date parsing with user-friendly error messages.

Fixes a real crash bug: date.fromisoformat() raises a raw ValueError (which
bubbled up as an unhandled 500 error) whenever a date field submits a
non-ISO string — e.g. '01/05/2026' from a browser where the native date
picker fell back to free-text entry, or a flatpickr widget that failed to
load. Every route that parses a date form field should go through
parse_form_date() instead of calling date.fromisoformat() directly.
"""
from datetime import date


class DateFormatError(ValueError):
    """A date field was submitted in an unparseable format."""


class RequiredFieldError(ValueError):
    """A required field was left blank."""


def parse_form_date(value, field_label: str, required: bool = False) -> date | None:
    """Parse a YYYY-MM-DD form value, raising a friendly error on bad input.

    Returns None for blank/missing values unless required=True, in which
    case a RequiredFieldError is raised instead.
    """
    if not value or not value.strip():
        if required:
            raise RequiredFieldError(f"{field_label} is required.")
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        raise DateFormatError(
            f"Invalid date format for {field_label}. Please use YYYY-MM-DD, "
            f"or select a valid date from the date picker.")
