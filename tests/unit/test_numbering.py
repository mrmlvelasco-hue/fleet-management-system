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
