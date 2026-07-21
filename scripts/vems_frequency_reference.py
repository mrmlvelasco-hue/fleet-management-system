"""VEMS PM frequency code reference table, CORRECTED.

The source lookup table (as supplied) has real data-entry errors in
several rows: the Val/ValDesc columns don't match either the row's own
Description text or its Interval_Pidx grouping (2=Hours, 3=Time/
Month-Year-Week-Quarter, 4=Km — consistent for every OTHER row). Blindly
trusting the source table's ValDesc would silently mis-classify a
kilometer-based interval as hours- or years-based, corrupting every PM
schedule built from it. Cross-checked every row against its Description
text and Interval_Pidx group; fixes below are the ones needed to make
all three agree.

  Frequency_CD  Source ValDesc  Fixed ValDesc  Why
  10KMS         H               K              "Every 10,000 Km" + Pidx 4 (Km group) — H doesn't belong here
  1KM           H               K              "First 1000 Km" + Pidx 4
  20KMS         Y (Val=1)       K (Val=20000)  "Every 20,000 Km" + Pidx 4 — Val AND ValDesc both wrong
  35KMS         H               K              "Every 35,000 Km" + Pidx 4
  25KMS         M (Val=15)      K (Val=25000)  "Every 25,000 km" + Pidx 4 — Val AND ValDesc both wrong
  7.5KMS        H               K              "Every 7500 KM" + Pidx 4
  4.5MTH        Val=5           Val=4.5        "Every 4.5 Months" — Val was rounded/truncated to an integer

Every other row already agreed across all three checks and is kept
as-is. Units: M=Month, Y=Year, W=Week, Q=Quarter, K=Kilometer, H=Hour.
"""
from decimal import Decimal

# Days-per-unit for converting a time-based interval into interval_days,
# per the migration's explicit requirement ("month interval converted as
# number of days"). Deliberately simple, consistent equivalents rather
# than calendar-accurate variable-length months -- this matches how
# interval_days is used elsewhere in the app (a fixed integer, not a
# calendar-aware recurrence), and 30/365/7/90 are the standard
# approximations used for this kind of fleet PM scheduling.
DAYS_PER_UNIT = {"M": 30, "Y": 365, "W": 7, "Q": 90}

# Frequency_CD -> (Val, ValDesc), corrected per the header comment above.
FREQUENCY_REFERENCE = {
    "21MTH": (21, "M"), "3MTH": (3, "M"), "6MTH": (6, "M"),
    "1YR": (1, "Y"), "2YRS": (2, "Y"), "1WK": (1, "W"),
    "500HRS": (500, "H"),
    "5KMS": (5000, "K"),
    "10KMS": (10000, "K"),          # fixed: was ValDesc=H
    "1QTR": (1, "Q"),
    "18MTH": (18, "M"), "15MTH": (15, "M"),
    "1KM": (1000, "K"),             # fixed: was ValDesc=H
    "1MTH": (1, "M"),
    "20KMS": (20000, "K"),          # fixed: was Val=1, ValDesc=Y
    "2KMS": (2000, "K"),
    "2MTH": (2, "M"),
    "35KMS": (35000, "K"),          # fixed: was ValDesc=H
    "45KMS": (45000, "K"),
    "8MTH": (8, "M"),
    "8KMS": (8000, "K"),
    "25KMS": (25000, "K"),          # fixed: was Val=15, ValDesc=M
    "4.5MTH": (Decimal("4.5"), "M"),  # fixed: was Val=5 (rounded)
    "7.5KMS": (7500, "K"),          # fixed: was ValDesc=H
    "3KMS": (3000, "K"),
    "1.5KM": (1500, "K"),
}


def resolve_frequency(code) -> tuple:
    """Returns (value, unit) for a frequency code, or (None, None) if the
    code is empty/zero/unrecognized. Never raises -- an unrecognized code
    should be logged and skipped by the caller, not crash the whole
    import over one bad row."""
    if code is None:
        return None, None
    s = str(code).strip().upper()
    if s in ("", "0", "NONE", "NAN"):
        return None, None
    entry = FREQUENCY_REFERENCE.get(s)
    if entry is None:
        return None, None
    return entry


def to_interval_km(code) -> int:
    """None if the code isn't Km-based (or isn't recognized at all) --
    callers should NOT treat that the same as '0 km interval'."""
    value, unit = resolve_frequency(code)
    if unit != "K":
        return None
    return int(value)


def to_interval_days(code) -> int:
    """Converts a Month/Year/Week/Quarter-coded interval into a fixed
    number of days, per DAYS_PER_UNIT above. None if the code isn't
    time-based (or isn't recognized)."""
    value, unit = resolve_frequency(code)
    if unit not in DAYS_PER_UNIT:
        return None
    days = Decimal(str(value)) * DAYS_PER_UNIT[unit]
    return int(days)  # truncates e.g. 4.5 months * 30 = 135.0 exactly here


def to_interval_hours(code) -> int:
    """None if the code isn't Hour-based (or isn't recognized)."""
    value, unit = resolve_frequency(code)
    if unit != "H":
        return None
    return int(value)
