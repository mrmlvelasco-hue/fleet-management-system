"""User Maintenance carries the mobile access switch.

An administrator needs two things from this screen: to grant or revoke
the field app for one person, and to answer "who has mobile access"
without opening every user in turn. The checkbox does the first, the
column does the second.
"""
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
    for code in ("user.view", "user.create", "user.update"):
        role.permissions.append(_perm(code))
    u = User(username="admin1", email="a1@example.com",
             password_hash=hash_password("secret123"), is_active=True)
    u.roles.append(role)
    db.session.add(u)
    db.session.commit()
    return u


def _login(client, username="admin1"):
    return client.post("/login", data={
        "username": username, "password": "secret123"},
        follow_redirects=True)


def test_create_user_with_mobile_access(app, client, admin):
    _login(client)
    client.post("/admin/users/new", data={
        "username": "fielddriver", "email": "fd@example.com",
        "first_name": "Juan", "last_name": "Dela Cruz",
        "password": "secret12345", "mobile_access": "y",
    }, follow_redirects=True)

    created = User.query.filter_by(username="fielddriver").first()
    assert created is not None
    assert created.mobile_access is True


def test_create_user_without_mobile_access_defaults_off(app, client, admin):
    _login(client)
    client.post("/admin/users/new", data={
        "username": "deskclerk", "email": "dc@example.com",
        "first_name": "Maria", "last_name": "Santos",
        "password": "secret12345",
    }, follow_redirects=True)

    created = User.query.filter_by(username="deskclerk").first()
    assert created is not None
    assert created.mobile_access is False


def test_revoking_mobile_access_persists(app, client, admin):
    """The revoke path specifically. An unchecked checkbox posts
    nothing at all, so a form that only ever reads a present value can
    grant access but never take it away -- which would make the kill
    switch one-directional."""
    target = User(username="fielddriver", email="fd@example.com",
                  password_hash=hash_password("secret123"),
                  is_active=True, mobile_access=True)
    db.session.add(target)
    db.session.commit()
    target_id = target.id

    _login(client)
    client.post(f"/admin/users/{target_id}/edit", data={
        "username": "fielddriver", "email": "fd@example.com",
        "first_name": "Juan", "last_name": "Dela Cruz",
    }, follow_redirects=True)

    assert db.session.get(User, target_id).mobile_access is False


def test_granting_mobile_access_on_edit_persists(app, client, admin):
    target = User(username="fielddriver", email="fd@example.com",
                  password_hash=hash_password("secret123"),
                  is_active=True, mobile_access=False)
    db.session.add(target)
    db.session.commit()
    target_id = target.id

    _login(client)
    client.post(f"/admin/users/{target_id}/edit", data={
        "username": "fielddriver", "email": "fd@example.com",
        "first_name": "Juan", "last_name": "Dela Cruz",
        "mobile_access": "y",
    }, follow_redirects=True)

    assert db.session.get(User, target_id).mobile_access is True


def test_form_renders_the_checkbox(app, client, admin):
    _login(client)
    html = client.get("/admin/users/new").get_data(as_text=True)
    assert 'name="mobile_access"' in html


def test_list_shows_who_has_mobile_access(app, client, admin):
    db.session.add(User(username="fielddriver", email="fd@example.com",
                        password_hash=hash_password("x"), is_active=True,
                        mobile_access=True))
    db.session.commit()

    _login(client)
    html = client.get("/admin/users").get_data(as_text=True)

    assert "Mobile" in html
