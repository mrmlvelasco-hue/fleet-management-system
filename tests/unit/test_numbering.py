import pytest

from app.core.numbering.numbering_service import AutoNumberingService, NoSchemeError
from app.modules.document_config.service import (
    DocumentTypeService, NumberingSchemeService)


@pytest.fixture()
def tt_scheme(db):
    dt = DocumentTypeService().create(code="TT", name="Trip Ticket",
                                      auto_numbering=True)
    NumberingSchemeService().create(
        document_type_id=dt.id, prefix="TT", include_year=True,
        include_month=False, digit_count=6, reset_policy="YEARLY")
    return dt


def test_generate_basic_format(db, tt_scheme, monkeypatch):
    svc = AutoNumberingService()
    monkeypatch.setattr(svc, "_now", lambda: (2026, 7))
    assert svc.generate("TT") == "TT-2026-000001"
    db.session.commit()
    assert svc.generate("TT") == "TT-2026-000002"


def test_generate_with_month_and_suffix(db):
    dt = DocumentTypeService().create(code="MO", name="Maintenance Order",
                                      auto_numbering=True)
    NumberingSchemeService().create(
        document_type_id=dt.id, prefix="MO", suffix="FMS", include_year=True,
        include_month=True, digit_count=4, reset_policy="MONTHLY")
    svc = AutoNumberingService()
    svc._now = lambda: (2026, 7)
    assert svc.generate("MO") == "MO-2026-07-0001-FMS"


def test_yearly_reset(db, tt_scheme):
    svc = AutoNumberingService()
    svc._now = lambda: (2026, 7)
    svc.generate("TT")
    svc.generate("TT")
    db.session.commit()
    svc._now = lambda: (2027, 1)
    assert svc.generate("TT") == "TT-2027-000001"


def test_monthly_reset(db):
    dt = DocumentTypeService().create(code="PR", name="Purchase Request",
                                      auto_numbering=True)
    NumberingSchemeService().create(
        document_type_id=dt.id, prefix="PR", include_year=True,
        include_month=True, digit_count=6, reset_policy="MONTHLY")
    svc = AutoNumberingService()
    svc._now = lambda: (2026, 7)
    svc.generate("PR")
    db.session.commit()
    svc._now = lambda: (2026, 8)
    assert svc.generate("PR") == "PR-2026-08-000001"


def test_never_reset_continues_across_years(db):
    dt = DocumentTypeService().create(code="ATD", name="Authority To Drive",
                                      auto_numbering=True)
    NumberingSchemeService().create(
        document_type_id=dt.id, prefix="ATD", include_year=True,
        include_month=False, digit_count=6, reset_policy="NEVER")
    svc = AutoNumberingService()
    svc._now = lambda: (2026, 12)
    svc.generate("ATD")
    db.session.commit()
    svc._now = lambda: (2027, 1)
    assert svc.generate("ATD") == "ATD-2027-000002"


def test_no_scheme_raises(db):
    DocumentTypeService().create(code="XX", name="No scheme")
    with pytest.raises(NoSchemeError):
        AutoNumberingService().generate("XX")


def test_preview_does_not_consume(db, tt_scheme):
    scheme = tt_scheme.numbering_scheme
    p = NumberingSchemeService.preview(scheme, sample_number=1, year=2026)
    assert p == "TT-2026-000001"
    svc = AutoNumberingService()
    svc._now = lambda: (2026, 7)
    assert svc.generate("TT") == "TT-2026-000001"


def test_counter_survives_a_rollback_in_the_callers_transaction(db, tt_scheme):
    """The actual bug behind a real production crash: the counter used
    to only be flushed, not committed, so a caller that rolled back
    after a failed insert (e.g. retrying a numbering collision) also
    undid the counter's own advance -- the very next generate() call
    would return the SAME number that just failed, forever. The
    counter must be durable the instant generate() returns, independent
    of anything the caller does with its own transaction afterward."""
    svc = AutoNumberingService()
    first = svc.generate("TT")

    # Simulate the caller rolling back (e.g. because the number it just
    # got collided with an existing row) WITHOUT committing anything.
    db.session.rollback()

    second = svc.generate("TT")
    assert second != first, (
        "the counter's own advance was undone by the caller's rollback")


def test_counter_advance_does_not_depend_on_the_caller_committing(db, tt_scheme):
    """generate() must not require the caller to ever commit for the
    counter to have genuinely moved -- confirmed by checking the
    counter's real database value directly after generate() returns,
    with nothing committed on the calling session at all."""
    from app.modules.document_config.models import NumberingCounter

    svc = AutoNumberingService()
    svc.generate("TT")
    # Nothing committed on db.session at all -- a fresh query must
    # still see the advance, because it was committed independently.
    db.session.rollback()
    counter = NumberingCounter.query.first()
    assert counter is not None
    assert counter.last_number == 1
