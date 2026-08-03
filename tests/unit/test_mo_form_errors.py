"""MO creation form: usable validation errors.

Three reported problems, all on the error path:
  * a decimal in a whole-number field surfaced Python's own
    "invalid literal for int() with base 10: '15.4'" -- which names
    neither the field nor what to do;
  * any validation failure cleared the entire form, so a single bad
    field meant retyping everything (worst with the category /
    transaction-type mismatch, which is easy to hit);
  * an initiator viewing their own locked order saw the Edit button
    simply vanish, with nothing saying why.
"""
from app.modules.transactions.routes import _friendly_number_error


class _Form(dict):
    pass


def test_decimal_in_a_whole_number_field_names_that_field():
    exc = ValueError("invalid literal for int() with base 10: '15.4'")
    msg = _friendly_number_error(exc, _Form(odometer_at_service="15.4"))
    assert "Odometer at Service" in msg
    assert "15.4" in msg
    assert "whole number" in msg


def test_raw_python_wording_never_reaches_the_person():
    exc = ValueError("invalid literal for int() with base 10: '15.4'")
    msg = _friendly_number_error(exc, _Form(odometer_at_service="15.4"))
    assert "invalid literal" not in msg
    assert "base 10" not in msg


def test_unmatched_value_still_gives_useful_guidance():
    """If the offending value can't be tied back to a field, the message
    must still point somewhere rather than being generic noise."""
    exc = ValueError("invalid literal for int() with base 10: 'zzz'")
    msg = _friendly_number_error(exc, _Form(odometer_at_service="12"))
    assert "numeric" in msg.lower()
    assert "odometer" in msg.lower()


def test_year_field_is_named_when_it_is_the_offender():
    exc = ValueError("invalid literal for int() with base 10: '20x6'")
    msg = _friendly_number_error(exc, _Form(year="20x6"))
    assert "Year" in msg
