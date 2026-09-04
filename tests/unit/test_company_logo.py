"""The configurable print letterhead logo.

`logo_filename` had existed on CompanyProfile since the beginning and
NOTHING wrote it, served it, or rendered it -- a column that looked like
a feature. The client asked to change the logo for their own branding,
which is when a decorative column becomes a missing one.
"""
import io
import json

import pytest
from werkzeug.datastructures import FileStorage

from app.core.security.password import hash_password
from app.extensions import db
from app.modules.api.company_letterhead import company_letterhead
from app.modules.user_management.models import Permission, Role, User

PNG = b"\x89PNG\r\n\x1a\n fake image bytes"


def _perm(code):
    p = Permission.query.filter_by(code=code).first()
    if p is None:
        module, action = code.split(".")
        p = Permission(code=code, module=module, action=action)
        db.session.add(p)
        db.session.flush()
    return p


def _user(username, codes):
    role = Role(name=f"r-{username}", description="r")
    db.session.add(role)
    db.session.flush()
    for c in codes:
        role.permissions.append(_perm(c))
    u = User(username=username, email=f"{username}@example.com",
             password_hash=hash_password("secret123"), is_active=True)
    u.roles.append(role)
    db.session.add(u)
    db.session.commit()
    return u


@pytest.fixture()
def company(app):
    from app.modules.system_admin.services.company_service import (
        CompanyProfileService)
    c = CompanyProfileService().save(
        company_name="Excellence Poultry & Livestock Specialist Inc.",
        address_line1="Terraverde", city="Carmona")
    db.session.commit()
    return c


@pytest.fixture()
def admin(app):
    return _user("cadmin", ["company.view", "company.update"])


@pytest.fixture()
def driver(app):
    # No company.* at all -- an ordinary user who prints documents.
    return _user("juan", ["tripticket.view"])


def _hdr(client, username="cadmin"):
    r = client.post("/api/v1/auth/token",
                    json={"username": username, "password": "secret123"})
    return {"Authorization": f"Bearer "
            f"{json.loads(r.get_data(as_text=True))['access_token']}"}


def _upload(client, name="logo.png", data=PNG):
    return client.post("/api/v1/admin/company/logo", headers=_hdr(client),
                       data={"file": (io.BytesIO(data), name)},
                       content_type="multipart/form-data")


def test_letterhead_has_no_logo_url_before_upload(app, company):
    assert company_letterhead()["logo_url"] is None


def test_upload_sets_the_logo(app, client, company, admin):
    assert _upload(client).status_code == 201
    assert company_letterhead()["logo_url"] == "/api/v1/company/logo"


def test_the_image_serves_back_exactly(app, client, company, admin):
    _upload(client)
    r = client.get("/api/v1/company/logo", headers=_hdr(client))
    assert r.status_code == 200
    assert r.get_data() == PNG


def test_anyone_signed_in_can_render_the_letterhead(app, client, company,
                                                     admin, driver):
    """No permission code, matching the rest of the letterhead. Gating a
    printed header behind company.view would mean handing that admin
    permission to everyone who prints a trip ticket, or shipping blank
    headers."""
    _upload(client)
    assert client.get("/api/v1/company/logo",
                      headers=_hdr(client, "juan")).status_code == 200


def test_uploading_requires_company_update(app, client, company, driver):
    r = client.post("/api/v1/admin/company/logo", headers=_hdr(client, "juan"),
                    data={"file": (io.BytesIO(PNG), "logo.png")},
                    content_type="multipart/form-data")
    assert r.status_code == 403


def test_a_non_image_is_refused(app, client, company, admin):
    r = _upload(client, name="notes.txt", data=b"hello")
    assert r.status_code == 400


def test_the_logo_can_be_replaced(app, client, company, admin):
    _upload(client)
    second = b"\x89PNG\r\n\x1a\n second image"
    _upload(client, name="new.png", data=second)

    r = client.get("/api/v1/company/logo", headers=_hdr(client))
    assert r.get_data() == second


def test_the_logo_can_be_cleared(app, client, company, admin):
    """Falls the letterhead back to text, rather than leaving a broken
    image on every printed document."""
    _upload(client)
    assert client.delete("/api/v1/admin/company/logo",
                         headers=_hdr(client)).status_code == 200
    assert company_letterhead()["logo_url"] is None
    assert client.get("/api/v1/company/logo",
                      headers=_hdr(client)).status_code == 404


def test_serving_with_no_company_configured_is_a_404_not_a_crash(app, client,
                                                                  admin):
    assert client.get("/api/v1/company/logo",
                      headers=_hdr(client)).status_code == 404


def test_the_company_payload_reports_the_logo(app, client, company, admin):
    """So the Company Profile screen can show a preview without a
    second request, and can tell 'no logo yet' from 'failed to load'."""
    before = json.loads(client.get("/api/v1/admin/company",
                                   headers=_hdr(client)).get_data(as_text=True))
    assert before["logo_url"] is None
    assert before["logo_filename"] is None

    _upload(client, name="brand.png")

    after = json.loads(client.get("/api/v1/admin/company",
                                  headers=_hdr(client)).get_data(as_text=True))
    assert after["logo_url"] == "/api/v1/company/logo"
    assert after["logo_filename"] == "brand.png"


def test_saving_the_profile_does_not_clear_the_logo(app, client, company,
                                                     admin):
    """The logo is uploaded separately from the text fields, so a PUT
    that knows nothing about it must leave it alone -- otherwise editing
    a phone number would silently strip the letterhead."""
    _upload(client)
    client.put("/api/v1/admin/company", headers=_hdr(client),
               json={"company_name": "Excellence", "phone": "0917"})

    assert company_letterhead()["logo_url"] == "/api/v1/company/logo"
