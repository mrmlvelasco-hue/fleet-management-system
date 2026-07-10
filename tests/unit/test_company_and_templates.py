import pytest
from app.modules.system_admin.services.company_service import (
    CompanyProfileService, SingletonError)
from app.modules.system_admin.services.email_template_service import (
    EmailTemplateService)
from app.modules.system_admin.models import CompanyProfile, EmailTemplate


# ---------- Company Profile ----------

def test_save_and_get_company_profile(db):
    svc = CompanyProfileService()
    svc.save(company_name="FMS Corp", city="Manila", tin="123-456-789")
    profile = svc.get()
    assert profile.company_name == "FMS Corp"
    assert profile.city == "Manila"


def test_save_updates_existing_profile(db):
    svc = CompanyProfileService()
    svc.save(company_name="Old Name")
    svc.save(company_name="New Name")
    assert CompanyProfile.query.filter_by(is_active=True).count() == 1
    assert svc.get().company_name == "New Name"


def test_get_returns_none_when_no_profile(db):
    assert CompanyProfileService().get() is None


# ---------- Email Templates ----------

def test_create_and_get_by_event(db):
    svc = EmailTemplateService()
    svc.create(event_code="submitted", name="Submitted",
               subject="Document submitted",
               body_html="<p>{{ title }}</p>", body_text="{{ title }}")
    tmpl = svc.get_by_event("submitted")
    assert tmpl is not None
    assert tmpl.subject == "Document submitted"


def test_render_template_with_context(db):
    svc = EmailTemplateService()
    svc.create(event_code="approved_final", name="Approved",
               subject="Approved: {{ doc_number }}",
               body_html="<p>{{ doc_number }} approved by {{ approver }}</p>",
               body_text="{{ doc_number }} approved")
    tmpl = svc.get_by_event("approved_final")
    result = svc.render(tmpl, {"doc_number": "TT-2026-000001",
                                "approver": "alice"})
    assert result["subject"] == "Approved: TT-2026-000001"
    assert "approved by alice" in result["body_html"]


def test_get_by_event_returns_none_when_missing(db):
    assert EmailTemplateService().get_by_event("nonexistent") is None
