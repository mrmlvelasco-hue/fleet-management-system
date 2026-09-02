"""Linking an assignee to a system account.

The rules exist because of what 1b will ask of this column: "which
vehicle belongs to the holder of this token". That question only has one
answer if one account resolves to at most one assignee, so the
uniqueness is a correctness requirement rather than tidiness.
"""
import pytest

from app.core.security.password import hash_password
from app.extensions import db
from app.modules.master_data.driver.models import Driver
from app.modules.master_data.driver.service import (
    DriverService, DuplicateAssigneeLinkError, InvalidAssigneeError)
from app.modules.master_data.org.models import Branch
from app.modules.user_management.models import User


@pytest.fixture()
def branch(app):
    b = Branch(code="CAR", name="Carmona")
    db.session.add(b)
    db.session.commit()
    return b


def _user(username, is_active=True):
    u = User(username=username, email=f"{username}@example.com",
             password_hash=hash_password("secret123"), is_active=is_active)
    db.session.add(u)
    db.session.commit()
    return u


def _driver(branch, employee_number, person_id):
    d = Driver(person_id=person_id, employee_number=employee_number,
               first_name="Juan", last_name="Dela Cruz",
               assignee_type="EMPLOYEE", branch_id=branch.id)
    db.session.add(d)
    db.session.commit()
    return d


def test_link_user_sets_the_account(app, branch):
    u = _user("jdelacruz")
    d = _driver(branch, "EMP-001", "PID-2026-000001")

    DriverService().link_user(d.id, u.id)

    assert db.session.get(Driver, d.id).user_id == u.id


def test_link_user_none_clears_the_link(app, branch):
    u = _user("jdelacruz")
    d = _driver(branch, "EMP-001", "PID-2026-000001")
    DriverService().link_user(d.id, u.id)

    DriverService().link_user(d.id, None)

    assert db.session.get(Driver, d.id).user_id is None
    # Unlinking is about the assignee record. The account itself is
    # untouched -- the person still signs in to the web app exactly as
    # before.
    assert db.session.get(User, u.id).is_active is True


def test_one_account_cannot_be_two_assignees(app, branch):
    u = _user("jdelacruz")
    first = _driver(branch, "EMP-001", "PID-2026-000001")
    second = _driver(branch, "EMP-002", "PID-2026-000002")
    DriverService().link_user(first.id, u.id)

    with pytest.raises(DuplicateAssigneeLinkError):
        DriverService().link_user(second.id, u.id)

    assert db.session.get(Driver, second.id).user_id is None


def test_relinking_the_same_account_is_a_no_op(app, branch):
    """Saving the assignee form twice without changing the picker must
    not fail the second time. Without this, an ordinary edit to an
    unrelated field on a linked assignee would be rejected as a
    duplicate."""
    u = _user("jdelacruz")
    d = _driver(branch, "EMP-001", "PID-2026-000001")
    DriverService().link_user(d.id, u.id)

    DriverService().link_user(d.id, u.id)

    assert db.session.get(Driver, d.id).user_id == u.id


def test_inactive_account_cannot_be_linked(app, branch):
    u = _user("resigned", is_active=False)
    d = _driver(branch, "EMP-001", "PID-2026-000001")

    with pytest.raises(InvalidAssigneeError):
        DriverService().link_user(d.id, u.id)


def test_unknown_account_cannot_be_linked(app, branch):
    d = _driver(branch, "EMP-001", "PID-2026-000001")

    with pytest.raises(InvalidAssigneeError):
        DriverService().link_user(d.id, 999999)


def test_linking_does_not_grant_mobile_access(app, branch):
    """The two facts stay independent. An assignee can be linked for
    reporting without being handed the field app."""
    u = _user("jdelacruz")
    d = _driver(branch, "EMP-001", "PID-2026-000001")

    DriverService().link_user(d.id, u.id)

    assert db.session.get(User, u.id).mobile_access is False


def test_link_user_on_a_missing_assignee_returns_none(app, branch):
    assert DriverService().link_user(999999, None) is None
