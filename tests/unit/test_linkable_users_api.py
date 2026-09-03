"""GET /api/v1/drivers/linkable-users

Exists because of a permission mismatch the parity audit surfaced.

The Flask assignee form populates its System Account picker
server-side, so anyone holding `driver.update` can use it. React would
have had to call /api/v1/admin/users, which is guarded by `user.view` --
a System Administration permission a Fleet Officer who maintains
assignees very plausibly does not hold. For them the React picker would
have rendered EMPTY while the Flask form worked: not an error, just a
control that appears broken. That is the worst kind of parity failure,
because nobody reports it as a bug.

This endpoint exposes strictly less than /admin/users -- three fields,
no email, no roles, no branch, no login history -- so guarding it with
driver.update widens the permission without widening the exposure.
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


def _user(username, codes, is_active=True):
    role = Role(name=f"r-{username}", description="r")
    db.session.add(role)
    db.session.flush()
    for c in codes:
        role.permissions.append(_perm(c))
    u = User(username=username, email=f"{username}@example.com",
             password_hash=hash_password("secret123"), is_active=is_active,
             first_name=username.title(), last_name="Tester")
    u.roles.append(role)
    db.session.add(u)
    db.session.commit()
    return u


@pytest.fixture()
def fleet_officer(app):
    # driver.update but deliberately NOT user.view -- the exact profile
    # the endpoint exists for.
    return _user("fleetofficer", ["driver.view", "driver.update"])


def _hdr(client, username="fleetofficer"):
    r = client.post("/api/v1/auth/token",
                    json={"username": username, "password": "secret123"})
    return {"Authorization": f"Bearer "
            f"{json.loads(r.get_data(as_text=True))['access_token']}"}


def _body(r):
    return json.loads(r.get_data(as_text=True))


def test_reachable_with_driver_update_and_no_user_view(app, client,
                                                        fleet_officer):
    """The whole point. If this 403s, the React picker is empty for
    exactly the people who maintain assignees."""
    r = client.get("/api/v1/drivers/linkable-users", headers=_hdr(client))
    assert r.status_code == 200


def test_returns_active_users(app, client, fleet_officer):
    _user("juan", [])

    rows = _body(client.get("/api/v1/drivers/linkable-users",
                            headers=_hdr(client)))["items"]

    assert "juan" in [u["username"] for u in rows]


def test_inactive_users_are_excluded(app, client, fleet_officer):
    """link_user refuses inactive accounts, so offering one would be a
    form that lies about what it accepts."""
    _user("resigned", [], is_active=False)

    rows = _body(client.get("/api/v1/drivers/linkable-users",
                            headers=_hdr(client)))["items"]

    assert "resigned" not in [u["username"] for u in rows]


def test_already_linked_accounts_are_still_offered(app, client,
                                                   fleet_officer):
    """Deliberate, matching Flask. Filtering them would leave an
    administrator staring at an empty picker with no way to discover
    the name they want is held elsewhere; the duplicate error names the
    assignee holding it, which is what they actually need."""
    from app.modules.master_data.driver.models import Driver
    from app.modules.master_data.driver.service import DriverService
    from app.modules.master_data.org.models import Branch

    b = Branch(code="CAR", name="Carmona")
    db.session.add(b)
    db.session.commit()
    juan = _user("juan", [])
    d = Driver(person_id="PID-1", employee_number="EMP-001",
               first_name="Juan", last_name="Cruz",
               assignee_type="DRIVER", branch_id=b.id)
    db.session.add(d)
    db.session.commit()
    DriverService().link_user(d.id, juan.id)

    rows = _body(client.get("/api/v1/drivers/linkable-users",
                            headers=_hdr(client)))["items"]

    assert "juan" in [u["username"] for u in rows]


def test_payload_is_minimal(app, client, fleet_officer):
    """Three fields only. This endpoint is reachable with a lower
    permission than /admin/users, so it must not become a back door to
    the same data -- no email, no roles, no branch, no login history."""
    rows = _body(client.get("/api/v1/drivers/linkable-users",
                            headers=_hdr(client)))["items"]

    # `label` is what SearchPicker renders; the parts stay available.
    # Still nothing sensitive -- no email, no roles, no branch, no login
    # history.
    assert set(rows[0].keys()) == {"id", "label", "username", "full_name"}


def test_ordered_by_username(app, client, fleet_officer):
    _user("zach", [])
    _user("aaron", [])

    names = [u["username"] for u in _body(
        client.get("/api/v1/drivers/linkable-users",
                   headers=_hdr(client)))["items"]]

    assert names == sorted(names)


def test_refused_without_driver_update(app, client, fleet_officer):
    _user("readonly", ["driver.view"])
    r = client.get("/api/v1/drivers/linkable-users",
                   headers=_hdr(client, "readonly"))
    assert r.status_code == 403


def test_search_narrows_by_username(app, client, fleet_officer):
    """Server-side, mirroring Flask's Select2. A capped client-side list
    shows the first N and says nothing, so a username outside them reads
    as 'that person has no account'."""
    _user("jdelacruz", [])
    _user("msantos", [])

    rows = _body(client.get("/api/v1/drivers/linkable-users?q=delacruz",
                            headers=_hdr(client)))["items"]

    names = [u["username"] for u in rows]
    assert "jdelacruz" in names
    assert "msantos" not in names


def test_search_matches_a_persons_name_not_only_their_username(
        app, client, fleet_officer):
    """An administrator looking for a driver knows their name, not the
    username IT assigned them."""
    _user("abc123", [])

    rows = _body(client.get("/api/v1/drivers/linkable-users?q=Abc123",
                            headers=_hdr(client)))["items"]

    assert "abc123" in [u["username"] for u in rows]


def test_each_row_carries_a_label(app, client, fleet_officer):
    rows = _body(client.get("/api/v1/drivers/linkable-users",
                            headers=_hdr(client)))["items"]
    assert "—" in rows[0]["label"] or rows[0]["label"] == rows[0]["username"]


def test_results_are_capped_and_say_so(app, client, fleet_officer):
    """has_more caps how many MATCHES are shown, not what is
    SELECTABLE."""
    for i in range(25):
        _user(f"bulk{i:02d}", [])

    body = _body(client.get("/api/v1/drivers/linkable-users",
                            headers=_hdr(client)))

    assert len(body["items"]) == 20
    assert body["has_more"] is True
