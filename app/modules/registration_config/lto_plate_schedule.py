"""LTO (Land Transportation Office) plate-based renewal schedule.

Philippine-specific rule engine, deliberately isolated in its own module
(rather than inlined in RegistrationDueCalculationService) so a future
configurable-per-country/region version can replace just this file
without touching the due-calculation logic that calls it — see the
Future Expansion note in the original spec.

Rules (as specified):
  - Renewal MONTH comes from the plate's LAST digit:
    1=Jan 2=Feb 3=Mar 4=Apr 5=May 6=Jun 7=Jul 8=Aug 9=Sep 0=Oct
  - Renewal WEEK comes from the plate's SECOND-TO-LAST digit:
    1,2,3 = Week 1 | 4,5,6 = Week 2 | 7,8 = Week 3 | 9,0 = Week 4
"""
import calendar
from datetime import date

_MONTH_BY_LAST_DIGIT = {
    1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 6, 7: 7, 8: 8, 9: 9, 0: 10,
}

_WEEK_BY_SECOND_LAST_DIGIT = {
    1: 1, 2: 1, 3: 1,
    4: 2, 5: 2, 6: 2,
    7: 3, 8: 3,
    9: 4, 0: 4,
}

_MONTH_NAMES = ["", "January", "February", "March", "April", "May", "June",
               "July", "August", "September", "October", "November",
               "December"]


def _digits_reversed(plate_number: str) -> list:
    """Digits of a plate number, rightmost first. 'AKA-7134' -> [4,3,1,7]."""
    if not plate_number:
        return []
    return [int(c) for c in reversed(plate_number) if c.isdigit()]


def get_plate_schedule(plate_number: str) -> dict:
    """LTO month/week derived from a plate's last two digits. Returns
    None if the plate has no digits at all (e.g. a bare conduction
    number with letters only, or the vehicle has no plate yet) — callers
    treat that as "no schedule available", not an error."""
    digits = _digits_reversed(plate_number)
    if not digits:
        return None

    last_digit = digits[0]
    second_last_digit = digits[1] if len(digits) > 1 else None

    month = _MONTH_BY_LAST_DIGIT.get(last_digit)
    if month is None:
        return None
    week = (_WEEK_BY_SECOND_LAST_DIGIT.get(second_last_digit)
           if second_last_digit is not None else None)

    return {
        "last_digit": last_digit,
        "month": month,
        "month_name": _MONTH_NAMES[month],
        "week": week,
    }


def calculate_due_date_from_plate(plate_number: str, year: int):
    """Estimated LTO renewal due date for `year` from the plate's
    schedule. The day-of-month is the last day of the indicated week
    block (day 7/14/21/28), capped to the actual number of days in that
    month. If the plate yields no week (e.g. only one digit total),
    defaults to the fourth/last week of the month. Returns None if no
    schedule could be derived at all."""
    schedule = get_plate_schedule(plate_number)
    if schedule is None:
        return None
    week = schedule["week"] or 4
    month = schedule["month"]
    days_in_month = calendar.monthrange(year, month)[1]
    day = min(week * 7, days_in_month)
    return date(year, month, day)


def next_due_date_from_plate(plate_number: str, as_of_date: date):
    """The next upcoming occurrence of the plate's schedule from
    `as_of_date` — this year's date if it hasn't passed yet, otherwise
    next year's. Used for the "no registration record yet" case, where
    there's no stored expiry year to anchor against."""
    candidate = calculate_due_date_from_plate(plate_number, as_of_date.year)
    if candidate is None:
        return None
    if candidate < as_of_date:
        return calculate_due_date_from_plate(plate_number, as_of_date.year + 1)
    return candidate
