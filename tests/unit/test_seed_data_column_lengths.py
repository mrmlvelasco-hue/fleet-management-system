"""Regression test for a real production bug: SQLite (used for local dev
and this test suite) does not enforce VARCHAR length limits, so a seeded
value exceeding its column's real max length passes silently here but
fails hard against MySQL (production) with a DataError -- exactly what
happened with BUDGET_TRACKING_MODE's description exceeding
SystemParameter.description's VARCHAR(255).

This test introspects each model's ACTUAL column length via SQLAlchemy
metadata (not a hardcoded number) and checks every seeded row against it,
so it stays correct if a column's length is ever changed, and it would
have caught the BUDGET_TRACKING_MODE bug before it ever reached a real
MySQL server.
"""
from app.cli import (_seed_system_parameters, _seed_email_templates,
                     _seed_atr_numbering)
from app.modules.system_admin.models import SystemParameter, EmailTemplate
from app.modules.system_admin.services.report_registry_service import (
    ReportRegistryService)
from app.modules.system_admin.models import ReportConfig
from app.modules.document_config.models import DocumentType


def _assert_fits(model, column_name, rows_query):
    max_len = getattr(model.__table__.c, column_name).type.length
    assert max_len is not None, (
        f"{model.__name__}.{column_name} has no length limit to check "
        f"against -- update this test if the column type changed.")
    too_long = []
    for row in rows_query.all():
        value = getattr(row, column_name) or ""
        if len(value) > max_len:
            identifier = (getattr(row, "code", None)
                         or getattr(row, "event_code", None)
                         or getattr(row, "report_code", "?"))
            too_long.append((identifier, len(value), max_len))
    assert not too_long, (
        f"Seeded {model.__name__}.{column_name} exceeds its VARCHAR({max_len}) "
        f"column limit -- SQLite silently allows this but MySQL (production) "
        f"will reject it with a DataError on `flask seed all`: {too_long}")


def test_all_seeded_system_parameter_descriptions_fit_the_column(db):
    _seed_system_parameters()
    db.session.commit()
    _assert_fits(SystemParameter, "description", SystemParameter.query)


def test_all_seeded_email_template_names_fit_the_column(db):
    _seed_email_templates()
    db.session.commit()
    _assert_fits(EmailTemplate, "name", EmailTemplate.query)


def test_all_seeded_email_template_subjects_fit_the_column(db):
    _seed_email_templates()
    db.session.commit()
    _assert_fits(EmailTemplate, "subject", EmailTemplate.query)


def test_all_seeded_report_config_descriptions_fit_the_column(db):
    ReportRegistryService().seed_builtin()
    db.session.commit()
    _assert_fits(ReportConfig, "description", ReportConfig.query)


def test_all_seeded_document_type_descriptions_fit_the_column(db):
    _seed_atr_numbering()  # seeds the ATR/ADR document types
    db.session.commit()
    _assert_fits(DocumentType, "description", DocumentType.query)
