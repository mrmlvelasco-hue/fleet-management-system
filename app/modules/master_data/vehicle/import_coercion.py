"""Value coercion helpers for the vehicle bulk importer (v48).

These exist because a migration workbook is never as clean as the template
promises. openpyxl(data_only=True) may hand back a real ``datetime`` for a
date-typed cell, a string with a SQL-Server time suffix
(``2011-10-17 00:00:00.000``) for a text-typed one, or a locale-formatted
``MM/DD/YYYY``. The pre-v48 importer only handled a bare ``YYYY-MM-DD``
string, so every other shape silently became NULL with no row error — the
"acquisition_date not saving" defect.

Two principles carried from the importer's own design notes:
  * Never null silently. An unparseable value is a *reported* row error,
    so the person can fix the sheet, not a quiet data loss.
  * The importer is permissive about placeholders (N/A, 1, 0000000): it
    accepts the row but stores the placeholder as NULL, so the Data
    Quality Scorecard — not the upload gate — is what surfaces it.
"""
from datetime import date, datetime
from decimal import Decimal, InvalidOperation


class CoercionError(ValueError):
    """Raised when a value cannot be coerced. Carries a human message the
    importer puts straight into the row's ``problems`` list."""


# Values that mean "no data" even though the cell isn't empty. Compared
# case-insensitively against the stripped cell text. This is the seed list;
# once the Data Quality Settings module ships it becomes a configurable,
# per-field token list so admins can extend it without a code change.
_NULL_EQUIVALENT = {
    "n/a", "na", "n/a car lease", "n/a carlease", "none", "no cr",
    "0", "00", "000", "0000", "00000", "000000", "0000000",
}


def clean(value):
    """Trim to a non-empty string, or None."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def strip_placeholder(value):
    """A cleaned string, unless it's a known null-equivalent placeholder,
    in which case None. Used for free-text identifier columns
    (far_number, cr_number, supplier, leasing_company) so placeholders
    don't inflate the completeness score later."""
    text = clean(value)
    if text is None:
        return None
    if text.lower() in _NULL_EQUIVALENT:
        return None
    return text


def coerce_date(value, field_label):
    """Return a ``datetime.date`` or None. Raises CoercionError on a value
    that is present but unparseable — never returns None for bad input, so
    the caller can report it instead of losing it.

    Accepts: date/datetime objects (what openpyxl returns for a real date
    cell), and strings in YYYY-MM-DD, YYYY-MM-DD HH:MM:SS[.fff],
    YYYY/MM/DD, MM/DD/YYYY, and DD-MON-YYYY shapes.
    """
    if value is None:
        return None
    # openpyxl already parsed a genuine date cell into an object.
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value

    text = str(value).strip()
    if not text:
        return None

    # SQL-Server / ODBC dump: "2011-10-17 00:00:00.000" — take the date part.
    # Splitting on whitespace first lets one YYYY-MM-DD branch cover both the
    # date-only and the timestamped forms.
    head = text.split(" ")[0].split("T")[0]

    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%d/%m/%Y", "%d-%b-%Y"):
        try:
            return datetime.strptime(head, fmt).date()
        except ValueError:
            continue
    # Last resort: the full string in case the time suffix hid a parseable form.
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue

    raise CoercionError(
        f"{field_label} '{text}' is not a recognizable date "
        f"(use YYYY-MM-DD).")


def coerce_int(value, field_label, *, strip_ph=False):
    """Whole number or None. Handles '60,000' and '60000.0'. Raises on junk."""
    if strip_ph:
        text = strip_placeholder(value)
    else:
        text = clean(value)
    if text is None:
        return None
    try:
        return int(float(text.replace(",", "")))
    except (TypeError, ValueError):
        raise CoercionError(f"{field_label} '{text}' is not a whole number.")


def coerce_decimal(value, field_label, *, strip_ph=False):
    """Decimal or None. Raises on junk."""
    if strip_ph:
        text = strip_placeholder(value)
    else:
        text = clean(value)
    if text is None:
        return None
    try:
        return Decimal(text.replace(",", ""))
    except (InvalidOperation, TypeError, ValueError):
        raise CoercionError(f"{field_label} '{text}' is not a valid amount.")


def resolve_lookup(value, lookup_code, *, cache=None):
    """Map a free-text imported value (e.g. 'Gasoline', 'manual') to the
    canonical code stored in Lookup ``lookup_code`` (e.g. 'GASOLINE',
    'MANUAL'), matched case-insensitively against both the item code and
    its label.

    Why at import time: the edit form's <select> emits the canonical code
    as each option's value, and reports/filters/API all compare against the
    stored column. If the column holds 'Gasoline' but options are 'GASOLINE',
    the dropdown never shows as selected and filters silently miss the row.
    Normalizing once here keeps every downstream consumer correct.

    Defensive by design: if the lookup can't be loaded or the value doesn't
    match any item, the ORIGINAL cleaned string is returned unchanged. A
    display nicety must never drop data or fail an import.
    """
    text = clean(value)
    if text is None:
        return None

    table = cache if cache is not None else _load_lookup(lookup_code)
    if not table:                      # lookup missing/empty -> passthrough
        return text
    return table.get(text.lower(), text)


def _load_lookup(lookup_code):
    """Return {lowercased code/description: canonical code} for a lookup
    type, or {} if loading fails (import must survive a missing lookup).

    NOTE: this project's Lookup model is a SINGLE flat table (Lookup:
    lookup_type, code, description) -- not a two-table LookupType/
    LookupItem structure. Written against the wrong model names, the
    try/except below would swallow the ImportError and every call would
    silently pass through, resolving nothing while the import still
    reported success. Verified against the live model.
    """
    try:
        from app.modules.system_admin.models import Lookup
    except Exception:
        return {}
    try:
        mapping = {}
        for it in Lookup.query.filter_by(lookup_type=lookup_code).all():
            if it.code:
                mapping[str(it.code).lower()] = it.code
            if it.description:
                mapping.setdefault(str(it.description).lower(), it.code)
        return mapping
    except Exception:
        return {}
