"""/api/v1/me reports both new facts.

The mobile client needs each for a different reason.

`assignee` is what 1b will read to find the caller's own vehicle -- it
is the answer to "who is the holder of this token, as a person".

`mobile_access` is here so the app can say something true when the
answer is no. Without it the only signal is a bare 403 from the token
endpoint, which is indistinguishable from a wrong password to anyone
looking at the screen; a driver would retype their password until the
account locked. With it, a session that is already open can tell the
person their account is not enabled for the field app and who to ask.
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
def user(app):
    role = Role(name="Field", description="Field user")
    db.session.add(role)
    u = User(username="fielddriver", email="fd@example.com",
             password_hash=hash_password("secret123"), is_active=True,
             first_name="Juan", last_name="Dela Cruz", mobile_access=True)
    u.roles.append(role)
    db.session.add(u)
    db.session.commit()
    return u


def _token(client, username="fielddriver"):
    r = client.post("/api/v1/auth/token",
                    json={"username": username, "password": "secret123"})
    return json.loads(r.get_data(as_text=True))["access_token"]


def _me(client, token):
    r = client.get("/api/v1/me",
                   headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    return json.loads(r.get_data(as_text=True))


def test_me_reports_mobile_access(app, client, user):
    assert _me(client, _token(client))["mobile_access"] is True


def test_me_reports_mobile_access_false(app, client, user):
    user.mobile_access = False
    db.session.commit()
    # Fetched over a session opened while access still stood -- the
    # exact case this key exists for.
    assert _me(client, _token(client))["mobile_access"] is False


def test_me_assignee_is_null_when_unlinked(app, client, user):
    """Present and null, not absent. A client that has to distinguish
    'no assignee' from 'this server predates the field app' by a missing
    key ends up guessing."""
    body = _me(client, _token(client))
    assert "assignee" in body
    assert body["assignee"] is None


def test_me_returns_the_linked_assignee(app, client, user, branch):
    d = Driver(person_id="PID-2026-000001", employee_number="EMP-001",
               first_name="Juan", last_name="Dela Cruz",
               assignee_type="DRIVER", branch_id=branch.id)
    db.session.add(d)
    db.session.commit()
    DriverService().link_user(d.id, user.id)

    assignee = _me(client, _token(client))["assignee"]

    assert assignee["id"] == d.id
    assert assignee["person_id"] == "PID-2026-000001"
    assert assignee["employee_number"] == "EMP-001"
    assert assignee["full_name"] == "Juan Dela Cruz"


def test_me_still_returns_the_existing_keys(app, client, user):
    """Additive. Anything already reading this payload keeps working."""
    body = _me(client, _token(client))
    for key in ("id", "username", "full_name", "email", "roles",
                "permissions"):
        assert key in body
