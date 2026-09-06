"""Org scope assignment over the API.

The client reported that a user "assigned only to East Asia
Laboratories Inc." still saw every branch. The scope engine was not
broken -- covers() and the endpoints that use it are correct and tested.

The gap was that the assignment could not be MADE from React. Two
different things share the word "branch":

  users.branch_id   -- a dropdown on the User form. Home branch,
                       informational, read by nothing that filters.
  UserOrgScope      -- what covers() reads, assigned on a separate
                       Jinja screen with no API endpoint and no React
                       equivalent.

So an administrator working in React sets "Branch" on the user,
reasonably believes access is restricted, and never touches the record
that governs visibility. covers() then sees zero scope rows and returns
True for every branch -- which is the documented "no assignment =
global access" rule behaving exactly as designed, on a user the admin
believed they had restricted.

These endpoints let React make the assignment the rule expects.
"""
import json

import pytest

from app.core.security.password import hash_password
from app.extensions import db
from app.modules.master_data.org.models import Branch
from app.modules.user_management.models import Permission, Role, User
from app.modules.user_management.org_scope_service import UserOrgScopeService


def _perm(code):
    p = Permission.query.filter_by(code=code).first()
    if p is None:
        m, a = code.split(".")
        p = Permission(code=code, module=m, action=a)
        db.session.add(p)
        db.session.flush()
    return p


def _user(username, codes):
    role = Role(name=f"r-{username}", description="r")
    db.session.add(role)
    db.session.flush()
    for c in codes:
        role.permissions.append(_perm(c))
    u = User(username=username, email=f"{username}@e.com",
             password_hash=hash_password("secret123"), is_active=True)
    u.roles.append(role)
    db.session.add(u)
    db.session.commit()
    return u


@pytest.fixture()
def env(app):
    b1 = Branch(code="EAL", name="East Asia Laboratories Inc.")
    b2 = Branch(code="MNL", name="Manila")
    db.session.add_all([b1, b2])
    db.session.commit()
    admin = _user("admin", ["user.view", "user.update"])
    target = _user("lbautista", ["vehicle.view"])
    return admin, target, b1, b2


def _hdr(client, username="admin"):
    r = client.post("/api/v1/auth/token",
                    json={"username": username, "password": "secret123"})
    return {"Authorization": f"Bearer "
            f"{json.loads(r.get_data(as_text=True))['access_token']}"}


def _body(r):
    return json.loads(r.get_data(as_text=True))


def test_scopes_are_empty_to_begin_with(app, client, env):
    _a, target, _b1, _b2 = env
    body = _body(client.get(f"/api/v1/admin/users/{target.id}/org-scopes",
                            headers=_hdr(client)))
    assert body["items"] == []
    # Stated explicitly so the screen can WARN rather than show a blank
    # list that looks like a restriction.
    assert body["global_access"] is True


def test_assigning_a_branch_restricts_the_user(app, client, env):
    """The whole point. Before this endpoint there was no way to do
    this from React at all."""
    _a, target, b1, b2 = env

    r = client.post(f"/api/v1/admin/users/{target.id}/org-scopes",
                    headers=_hdr(client),
                    json={"scope_type": "BRANCH", "branch_id": b1.id})

    assert r.status_code == 201
    svc = UserOrgScopeService()
    assert svc.covers(target.id, branch_id=b1.id) is True
    assert svc.covers(target.id, branch_id=b2.id) is False


def test_the_payload_says_the_user_is_no_longer_global(app, client, env):
    _a, target, b1, _b2 = env
    client.post(f"/api/v1/admin/users/{target.id}/org-scopes",
                headers=_hdr(client),
                json={"scope_type": "BRANCH", "branch_id": b1.id})

    body = _body(client.get(f"/api/v1/admin/users/{target.id}/org-scopes",
                            headers=_hdr(client)))

    assert body["global_access"] is False
    assert body["items"][0]["branch_name"] == "East Asia Laboratories Inc."


def test_removing_the_last_scope_returns_the_user_to_global(app, client, env):
    """Worth being explicit about, because it is surprising: removing
    the only scope does not restrict further, it removes the
    restriction entirely."""
    _a, target, b1, b2 = env
    created = _body(client.post(
        f"/api/v1/admin/users/{target.id}/org-scopes", headers=_hdr(client),
        json={"scope_type": "BRANCH", "branch_id": b1.id}))

    r = client.delete(
        f"/api/v1/admin/users/{target.id}/org-scopes/{created['id']}",
        headers=_hdr(client))

    assert r.status_code == 200
    assert UserOrgScopeService().covers(target.id, branch_id=b2.id) is True


def test_a_user_can_hold_several_branches(app, client, env):
    _a, target, b1, b2 = env
    for b in (b1, b2):
        client.post(f"/api/v1/admin/users/{target.id}/org-scopes",
                    headers=_hdr(client),
                    json={"scope_type": "BRANCH", "branch_id": b.id})

    svc = UserOrgScopeService()
    assert svc.covers(target.id, branch_id=b1.id) is True
    assert svc.covers(target.id, branch_id=b2.id) is True


def test_a_branch_scope_without_a_branch_is_refused(app, client, env):
    _a, target, _b1, _b2 = env
    r = client.post(f"/api/v1/admin/users/{target.id}/org-scopes",
                    headers=_hdr(client), json={"scope_type": "BRANCH"})
    assert r.status_code == 400


def test_assigning_requires_user_update(app, client, env):
    """Deciding what someone may SEE is a user-administration act."""
    _a, target, b1, _b2 = env
    _user("nosy", ["user.view"])

    r = client.post(f"/api/v1/admin/users/{target.id}/org-scopes",
                    headers=_hdr(client, "nosy"),
                    json={"scope_type": "BRANCH", "branch_id": b1.id})

    assert r.status_code == 403


def test_an_unknown_user_is_a_404(app, client, env):
    r = client.get("/api/v1/admin/users/999999/org-scopes",
                   headers=_hdr(client))
    assert r.status_code == 404
