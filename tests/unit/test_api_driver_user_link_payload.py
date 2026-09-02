"""The Assignee API carries the linked system account.

Both shapes carry it, and both carry `username` alongside the id. The
id alone would force the list to fetch every user just to render one
column, and the whole reason the column exists is so an administrator
can see at a glance which assignees can sign in.
"""
import json

import pytest

from app.core.security.password import hash_password
from app.extensions import db
from app.modules.master_data.driver.models import Driver
from app.modules.master_data.driver.service import DriverService
from app.modules.master_data.org.models import Branch
from app.modules.user_management.models import Permission, Role, User


@pytest.fixture()
def branch(app):
    b = Branch(code="CAR", name="Carmona")
    db.session.add(b)
    db.session.commit()
    return b


@pytest.fixture()
def admin(app):
    role = Role(name="Fleet Admin", description="admin")
    db.session.add(role)
    db.session.flush()
    for code in ("driver.view",):
        perm = Permission.query.filter_by(code=code).first()
        if perm is None:
            module, action = code.split(".")
            perm = Permission(code=code, module=module, action=action)
            db.session.add(perm)
            db.session.flush()
        role.permissions.append(perm)
    u = User(username="fleetadmin", email="fa@example.com",
             password_hash=hash_password("secret123"), is_active=True)
    u.roles.append(role)
    db.session.add(u)
    db.session.commit()
    return u


@pytest.fixture()
def field_user(app):
    u = User(username="jdelacruz", email="jd@example.com",
             password_hash=hash_password("secret123"), is_active=True)
    db.session.add(u)
    db.session.commit()
    return u


def _driver(branch, employee_number="EMP-001",
            person_id="PID-2026-000001"):
    d = Driver(person_id=person_id, employee_number=employee_number,
               first_name="Juan", last_name="Dela Cruz",
               assignee_type="DRIVER", branch_id=branch.id)
    db.session.add(d)
    db.session.commit()
    return d


def _auth(client, username="fleetadmin"):
    r = client.post("/api/v1/auth/token",
                    json={"username": username, "password": "secret123"})
    token = json.loads(r.get_data(as_text=True))["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _get(client, url, headers):
    r = client.get(url, headers=headers)
    assert r.status_code == 200, r.get_data(as_text=True)
    return json.loads(r.get_data(as_text=True))


def test_list_row_carries_a_null_link_when_unlinked(app, client, admin,
                                                     branch):
    _driver(branch)
    row = _get(client, "/api/v1/drivers", _auth(client))["items"][0]
    assert row["user_id"] is None
    assert row["username"] is None


def test_list_row_carries_the_linked_account(app, client, admin, branch,
                                              field_user):
    d = _driver(branch)
    DriverService().link_user(d.id, field_user.id)

    row = _get(client, "/api/v1/drivers", _auth(client))["items"][0]

    assert row["user_id"] == field_user.id
    assert row["username"] == "jdelacruz"


def test_detail_carries_the_linked_account(app, client, admin, branch,
                                            field_user):
    d = _driver(branch)
    DriverService().link_user(d.id, field_user.id)

    body = _get(client, f"/api/v1/drivers/{d.id}", _auth(client))

    assert body["user_id"] == field_user.id
    assert body["username"] == "jdelacruz"


def test_detail_carries_a_null_link_when_unlinked(app, client, admin,
                                                   branch):
    d = _driver(branch)
    body = _get(client, f"/api/v1/drivers/{d.id}", _auth(client))
    assert body["user_id"] is None
    assert body["username"] is None
