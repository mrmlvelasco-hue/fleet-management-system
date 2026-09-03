"""The JSON users API carries mobile_access.

Phase 1a added the flag to the Jinja screens and stopped there, so the
React admin screen had no way to read or set it. A field that exists in
one UI and not the other is worse than one that exists in neither:
an administrator who grants mobile access in React, sees it not stick,
and concludes the feature is broken.
"""
import json

import pytest

from app.core.security.password import hash_password
from app.extensions import db
from app.modules.user_management.models import Permission, Role, User


def _perm(code):
    p = Permission.query.filter_by(code=code).first()
    if p is None:
        module, action = code.split(".")
        p = Permission(code=code, module=module, action=action)
        db.session.add(p)
        db.session.flush()
    return p


@pytest.fixture()
def admin(app):
    role = Role(name="Admin", description="admin")
    db.session.add(role)
    db.session.flush()
    for c in ("user.view", "user.create", "user.update"):
        role.permissions.append(_perm(c))
    u = User(username="admin1", email="a1@example.com",
             password_hash=hash_password("secret123"), is_active=True)
    u.roles.append(role)
    db.session.add(u)
    db.session.commit()
    return u


def _hdr(client):
    r = client.post("/api/v1/auth/token",
                    json={"username": "admin1", "password": "secret123"})
    tok = json.loads(r.get_data(as_text=True))["access_token"]
    return {"Authorization": f"Bearer {tok}"}


def _body(r):
    return json.loads(r.get_data(as_text=True))


def test_list_reports_mobile_access(app, client, admin):
    db.session.add(User(username="fielddriver", email="fd@example.com",
                        password_hash=hash_password("x"), is_active=True,
                        mobile_access=True))
    db.session.commit()

    rows = _body(client.get("/api/v1/admin/users", headers=_hdr(client)))["items"]
    by_name = {r["username"]: r for r in rows}

    assert by_name["fielddriver"]["mobile_access"] is True
    assert by_name["admin1"]["mobile_access"] is False


def test_create_accepts_mobile_access(app, client, admin):
    r = client.post("/api/v1/admin/users", headers=_hdr(client), json={
        "username": "newdriver", "email": "nd@example.com",
        "password": "secret12345", "mobile_access": True})

    assert r.status_code == 201
    assert _body(r)["mobile_access"] is True
    assert User.query.filter_by(
        username="newdriver").first().mobile_access is True


def test_create_defaults_to_no_mobile_access(app, client, admin):
    r = client.post("/api/v1/admin/users", headers=_hdr(client), json={
        "username": "deskclerk", "email": "dc@example.com",
        "password": "secret12345"})

    assert _body(r)["mobile_access"] is False


def test_update_can_grant_mobile_access(app, client, admin):
    t = User(username="fielddriver", email="fd@example.com",
             password_hash=hash_password("x"), is_active=True)
    db.session.add(t)
    db.session.commit()

    r = client.put(f"/api/v1/admin/users/{t.id}", headers=_hdr(client),
                   json={"mobile_access": True})

    assert r.status_code == 200
    assert db.session.get(User, t.id).mobile_access is True


def test_update_can_revoke_mobile_access(app, client, admin):
    """The revoke path. A partial-update endpoint that only ever acts on
    truthy values could grant access but never take it away -- and a
    kill switch that only turns on is not a kill switch."""
    t = User(username="fielddriver", email="fd@example.com",
             password_hash=hash_password("x"), is_active=True,
             mobile_access=True)
    db.session.add(t)
    db.session.commit()

    r = client.put(f"/api/v1/admin/users/{t.id}", headers=_hdr(client),
                   json={"mobile_access": False})

    assert r.status_code == 200
    assert db.session.get(User, t.id).mobile_access is False


def test_an_update_that_omits_the_key_leaves_it_alone(app, client, admin):
    """PATCH semantics. Absent means 'not supplied', not 'revoke' --
    otherwise editing someone's email would silently cut off their
    phone."""
    t = User(username="fielddriver", email="fd@example.com",
             password_hash=hash_password("x"), is_active=True,
             mobile_access=True)
    db.session.add(t)
    db.session.commit()

    client.put(f"/api/v1/admin/users/{t.id}", headers=_hdr(client),
               json={"email": "changed@example.com"})

    assert db.session.get(User, t.id).mobile_access is True
