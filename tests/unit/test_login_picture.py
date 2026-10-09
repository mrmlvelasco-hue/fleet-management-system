"""Login page picture -- uploaded by the client's administrator.

System Administration -> Company Profile -> "Login page picture": the
client can replace the picture on the right of the login page without a
code change or redeploy (each hosted client sets its own).

  POST   /api/v1/admin/company/login-picture   upload (company.update)
  DELETE /api/v1/admin/company/login-picture   restore the default
  GET    /api/v1/branding                      PUBLIC: {login_picture_url}
  GET    /api/v1/branding/login-picture        PUBLIC: the image itself

The login page is shown BEFORE sign-in, so the picture must be public --
but only that one picture. The company logo stays behind sign-in.
Only JPG/PNG up to 3 MB (a picture, not any attachment type the general
allow-list would accept).
"""
import io
import json

import pytest

from app.core.security.password import hash_password
from app.extensions import db
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
    c = CompanyProfileService().save(company_name="Excellence Poultry", city="Carmona")
    db.session.commit()
    return c


@pytest.fixture()
def admin(app):
    return _user("lpadmin", ["company.view", "company.update"])


def _hdr(client, username):
    r = client.post("/api/v1/auth/token",
                    json={"username": username, "password": "secret123"})
    return {"Authorization":
            f"Bearer {json.loads(r.get_data(as_text=True))['access_token']}"}


def _upload(client, username, data=PNG, name="hero.png"):
    return client.post("/api/v1/admin/company/login-picture",
                       headers=_hdr(client, username),
                       data={"file": (io.BytesIO(data), name)},
                       content_type="multipart/form-data")


def test_no_picture_yet_means_the_default(client, db, company):
    r = client.get("/api/v1/branding")
    assert r.status_code == 200
    assert r.get_json() == {"login_picture_url": None}
    assert client.get("/api/v1/branding/login-picture").status_code == 404


def test_upload_then_anyone_can_see_it_before_signing_in(client, db, company, admin):
    r = _upload(client, "lpadmin")
    assert r.status_code == 201, r.get_data(as_text=True)
    assert r.get_json()["login_picture_filename"] == "hero.png"
    url = client.get("/api/v1/branding").get_json()["login_picture_url"]
    assert url.startswith("/api/v1/branding/login-picture?v=")
    img = client.get(url)                       # no Authorization header
    assert img.status_code == 200
    assert img.data == PNG
    assert img.mimetype.startswith("image/")
    assert img.headers.get("X-Content-Type-Options") == "nosniff"


def test_a_new_upload_changes_the_url_so_browsers_refresh(client, db, company, admin):
    _upload(client, "lpadmin")
    first = client.get("/api/v1/branding").get_json()["login_picture_url"]
    _upload(client, "lpadmin", name="hero2.png")
    second = client.get("/api/v1/branding").get_json()["login_picture_url"]
    assert first != second


def test_company_profile_shows_the_current_picture(client, db, company, admin):
    _upload(client, "lpadmin")
    body = client.get("/api/v1/admin/company", headers=_hdr(client, "lpadmin")).get_json()
    assert body["login_picture_filename"] == "hero.png"
    assert body["login_picture_url"].startswith("/api/v1/branding/login-picture")


def test_restore_default(client, db, company, admin):
    _upload(client, "lpadmin")
    r = client.delete("/api/v1/admin/company/login-picture",
                      headers=_hdr(client, "lpadmin"))
    assert r.status_code == 200
    assert client.get("/api/v1/branding").get_json() == {"login_picture_url": None}
    assert client.get("/api/v1/branding/login-picture").status_code == 404


@pytest.mark.parametrize("name", ["notes.pdf", "sheet.xlsx", "anim.gif", "x.svg"])
def test_only_jpg_and_png_are_accepted(client, db, company, admin, name):
    r = _upload(client, "lpadmin", name=name)
    assert r.status_code == 400
    assert "JPG or PNG" in r.get_json()["message"]


def test_pictures_over_3_mb_are_refused(client, db, company, admin):
    r = _upload(client, "lpadmin", data=PNG + b"0" * (3 * 1024 * 1024))
    assert r.status_code == 400
    assert "3 MB" in r.get_json()["message"]


def test_uploading_needs_company_update(client, db, company):
    _user("lpviewer", ["company.view"])
    assert _upload(client, "lpviewer").status_code == 403


def test_the_company_logo_is_still_not_public(client, db, company, admin):
    """Only the login picture is public; the logo keeps its sign-in rule."""
    client.post("/api/v1/admin/company/logo", headers=_hdr(client, "lpadmin"),
                data={"file": (io.BytesIO(PNG), "logo.png")},
                content_type="multipart/form-data")
    assert client.get("/api/v1/company/logo").status_code == 401
