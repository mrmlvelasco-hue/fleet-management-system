from datetime import date

import pytest

from app.core.validation.date_utils import parse_form_date, DateFormatError


def test_parses_valid_iso_date():
    assert parse_form_date("2026-07-15", "Registration Date") == date(2026, 7, 15)


def test_empty_string_returns_none():
    assert parse_form_date("", "Registration Date") is None


def test_none_returns_none():
    assert parse_form_date(None, "Registration Date") is None


def test_invalid_slash_format_raises_friendly_error():
    with pytest.raises(DateFormatError) as exc_info:
        parse_form_date("01/05/2026", "Acquisition Date")
    assert "Acquisition Date" in str(exc_info.value)
    assert "YYYY-MM-DD" in str(exc_info.value)


def test_garbage_string_raises_friendly_error():
    with pytest.raises(DateFormatError) as exc_info:
        parse_form_date("not-a-date", "Valid From")
    assert "Valid From" in str(exc_info.value)


def test_required_true_raises_when_missing():
    from app.core.validation.date_utils import RequiredFieldError
    with pytest.raises(RequiredFieldError) as exc_info:
        parse_form_date("", "Registration Date", required=True)
    assert "Registration Date" in str(exc_info.value)
    assert "required" in str(exc_info.value).lower()


def test_parse_form_datetime_valid():
    from datetime import datetime
    from app.core.validation.date_utils import parse_form_datetime
    result = parse_form_datetime("2026-07-15T08:00", "Departure")
    assert result == datetime(2026, 7, 15, 8, 0)


def test_parse_form_datetime_invalid_raises_friendly_error():
    from app.core.validation.date_utils import parse_form_datetime
    with pytest.raises(DateFormatError) as exc_info:
        parse_form_datetime("07/15/2026 8:00 AM", "Departure")
    assert "Departure" in str(exc_info.value)


def test_parse_form_datetime_empty_returns_none():
    from app.core.validation.date_utils import parse_form_datetime
    assert parse_form_datetime("", "Departure") is None
