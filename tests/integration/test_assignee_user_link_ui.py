"""The Assignee form links a person to a system account.

Kept off the generic field path in DriverService.update() on purpose:
the link has rules, and a rule reachable through a second door is not a
rule. These tests assert that the route goes through link_user, by
checking that its errors surface as a flash message rather than a 500.
"""
import pytest

from app.core.security.password import hash_password
from app.extensions import db
from app.modules.master_data.driver.models import Driver
from app.modules.master_data.driver.service import DriverService
from app.modules.master_data.org.models import Branch
from app.modules.user_management.models import Permission, Role, User
from tests.conftest import tiny_jpeg_file


def _perm(code):
    p = Permission.query.filter_by(code=code).first()
    if p is None:
        module, action = code.split(".")
        p = Permission(code=code, module=module, action=action)
        db.session.add(p)
        db.session.flush()
    return p


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
    for code in ("driver.view", "driver.create", "driver.update"):
        role.permissions.append(_perm(code))
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
               assignee_type="EMPLOYEE", branch_id=branch.id,
               phone="0917", email="jd@example.com")
    db.session.add(d)
    db.session.commit()
    return d


def _login(client):
    return client.post("/login", data={"username": "fleetadmin",
                                       "password": "secret123"},
                       follow_redirects=True)


def _edit_payload(branch, **overrides):
    data = {
        "assignee_type": "EMPLOYEE",
        "first_name": "Juan", "last_name": "Dela Cruz",
        "middle_name": "", "phone": "0917", "email": "jd@example.com",
        "branch_id": str(branch.id),
    }
    data.update(overrides)
    return data


def test_edit_links_the_system_account(app, client, admin, branch,
                                        field_user):
    d = _driver(branch)
    _login(client)

    client.post(f"/master/drivers/{d.id}/edit",
                data=_edit_payload(branch, user_id=str(field_user.id)),
                follow_redirects=True)

    assert db.session.get(Driver, d.id).user_id == field_user.id


def test_edit_with_a_blank_picker_clears_the_link(app, client, admin,
                                                   branch, field_user):
    d = _driver(branch)
    DriverService().link_user(d.id, field_user.id)
    _login(client)

    client.post(f"/master/drivers/{d.id}/edit",
                data=_edit_payload(branch, user_id=""),
                follow_redirects=True)

    assert db.session.get(Driver, d.id).user_id is None


def test_duplicate_link_flashes_rather_than_500(app, client, admin,
                                                 branch, field_user):
    """An administrator picking a name already taken is an ordinary
    mistake and must read as one. Left to the UNIQUE constraint it would
    surface as an IntegrityError at commit -- a 500 page, three layers
    from the form they submitted."""
    first = _driver(branch, "EMP-001", "PID-2026-000001")
    second = _driver(branch, "EMP-002", "PID-2026-000002")
    DriverService().link_user(first.id, field_user.id)
    _login(client)

    r = client.post(f"/master/drivers/{second.id}/edit",
                    data=_edit_payload(branch, user_id=str(field_user.id)),
                    follow_redirects=True)

    assert r.status_code == 200
    assert "already linked" in r.get_data(as_text=True)
    assert db.session.get(Driver, second.id).user_id is None


def test_saving_twice_without_changing_the_picker_succeeds(app, client,
                                                            admin, branch,
                                                            field_user):
    """The re-link no-op, exercised through the form. Without it, any
    later edit to a linked assignee would be rejected as a duplicate of
    itself."""
    d = _driver(branch)
    _login(client)
    payload = _edit_payload(branch, user_id=str(field_user.id))

    client.post(f"/master/drivers/{d.id}/edit", data=payload,
                follow_redirects=True)
    r = client.post(f"/master/drivers/{d.id}/edit", data=payload,
                    follow_redirects=True)

    assert "already linked" not in r.get_data(as_text=True)
    assert db.session.get(Driver, d.id).user_id == field_user.id


def test_form_renders_the_picker(app, client, admin, branch, field_user):
    d = _driver(branch)
    _login(client)
    html = client.get(f"/master/drivers/{d.id}/edit").get_data(
        as_text=True)
    assert 'name="user_id"' in html
    assert "jdelacruz" in html


def test_list_shows_the_linked_account(app, client, admin, branch,
                                        field_user):
    d = _driver(branch)
    DriverService().link_user(d.id, field_user.id)
    _login(client)

    html = client.get("/master/drivers").get_data(as_text=True)

    assert "System Account" in html
    assert "jdelacruz" in html


def test_create_links_the_system_account(app, client, admin, branch,
                                          field_user):
    """The picker renders on the New form, so it must work there.
    Silently discarding the choice on create -- and only on create -- is
    a gap nobody reports; they just re-open the record and set it
    again."""
    _login(client)

    client.post("/master/drivers/new", data=_edit_payload(
        branch, employee_number="EMP-100",
        user_id=str(field_user.id), photo=tiny_jpeg_file()),
        follow_redirects=True, content_type="multipart/form-data")

    created = Driver.query.filter_by(employee_number="EMP-100").first()
    assert created is not None
    assert created.user_id == field_user.id


def test_create_still_works_with_no_account_picked(app, client, admin,
                                                    branch):
    _login(client)

    client.post("/master/drivers/new", data=_edit_payload(
        branch, employee_number="EMP-101", user_id="",
        photo=tiny_jpeg_file()),
        follow_redirects=True, content_type="multipart/form-data")

    created = Driver.query.filter_by(employee_number="EMP-101").first()
    assert created is not None
    assert created.user_id is None
