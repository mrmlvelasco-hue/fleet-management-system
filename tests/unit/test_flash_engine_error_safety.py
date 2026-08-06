"""Regression test for a real reported incident: a raw database error
(IntegrityError, including its full SQL statement, real column names,
and literal parameter values) was flashed verbatim to the end user
above the 500 page -- an information-disclosure bug independent of
whatever caused the underlying error in the first place.
"""
from sqlalchemy.exc import IntegrityError

from app.modules.transactions.routes import _flash_engine_error


def test_this_apps_own_business_exception_is_shown_verbatim(app):
    """Hand-written business-rule messages ARE meant for the person
    using the system and must still display exactly as written."""
    class FakeBusinessError(Exception):
        pass
    FakeBusinessError.__module__ = "app.modules.transactions.fake.service"

    with app.test_request_context():
        from flask import get_flashed_messages
        _flash_engine_error(FakeBusinessError(
            "This vehicle already has an open PM order."))
        msgs = get_flashed_messages()
    assert msgs == ["This vehicle already has an open PM order."]


def test_a_raw_database_error_does_not_leak_sql_to_the_user(app):
    """The exact reported bug: a raw IntegrityError's message contains
    the full INSERT statement and bound parameters -- none of that may
    reach the person using the system."""
    with app.test_request_context():
        from flask import get_flashed_messages
        try:
            raise IntegrityError(
                "INSERT INTO maintenance_orders (document_number, "
                "vehicle_id) VALUES (%(document_number)s, %(vehicle_id)s)",
                {"document_number": "MO-2026-000002", "vehicle_id": 129},
                Exception("Duplicate entry"))
        except IntegrityError as exc:
            _flash_engine_error(exc)
        msgs = get_flashed_messages()

    assert len(msgs) == 1
    assert "INSERT INTO" not in msgs[0]
    assert "document_number" not in msgs[0]
    assert "MO-2026-000002" not in msgs[0]
    assert "vehicle_id" not in msgs[0]
    # Still genuinely useful: a reference the person can quote back.
    assert "reference" in msgs[0].lower()


def test_a_raw_third_party_exception_gets_the_same_safe_treatment(app):
    """Any exception NOT defined in this codebase -- not just
    SQLAlchemy's -- must get the generic message, not just database
    errors specifically."""
    class SomeLibraryError(Exception):
        pass
    SomeLibraryError.__module__ = "some_external_package.errors"

    with app.test_request_context():
        from flask import get_flashed_messages
        _flash_engine_error(SomeLibraryError("internal library detail"))
        msgs = get_flashed_messages()

    assert "internal library detail" not in msgs[0]
    assert "reference" in msgs[0].lower()
