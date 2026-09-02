"""Schema for Phase 1a: the mobile channel flag and the assignee link.

Both columns are additive and both DEFAULT to the state the system was
in before they existed -- no mobile access, no link. Asserted here
rather than assumed, because the whole safety argument for adding a
gate to a live auth endpoint rests on the default being the closed one.
"""
from datetime import date

from app.extensions import db
from app.modules.master_data.driver.models import Driver
from app.modules.master_data.org.models import Branch
from app.modules.user_management.models import User


def _user(username="fielduser"):
    u = User(username=username, email=f"{username}@example.com",
             password_hash="x", first_name="Field", last_name="User")
    db.session.add(u)
    db.session.commit()
    return u


def _driver(branch, **kwargs):
    d = Driver(person_id=kwargs.pop("person_id", "PID-2026-000001"),
               employee_number=kwargs.pop("employee_number", "EMP-001"),
               first_name="Juan", last_name="Dela Cruz",
               assignee_type="DRIVER", branch_id=branch.id, **kwargs)
    db.session.add(d)
    db.session.commit()
    return d


def _branch():
    b = Branch(code="CAR", name="Carmona")
    db.session.add(b)
    db.session.commit()
    return b


def test_user_defaults_to_no_mobile_access(app):
    """The secure default. An existing install that runs the migration
    and changes nothing else grants nobody native access."""
    u = _user()
    assert u.mobile_access is False


def test_mobile_access_can_be_granted(app):
    u = _user()
    u.mobile_access = True
    db.session.commit()
    assert db.session.get(User, u.id).mobile_access is True


def test_driver_user_link_defaults_to_none(app):
    d = _driver(_branch())
    assert d.user_id is None
    assert d.user_account is None


def test_driver_user_link_resolves_to_the_account(app):
    branch = _branch()
    u = _user()
    d = _driver(branch)
    d.user_id = u.id
    db.session.commit()

    fetched = db.session.get(Driver, d.id)
    assert fetched.user_account.username == "fielduser"


def test_linking_does_not_grant_mobile_access(app):
    """Two independent facts. A link records WHO someone is; the flag
    records WHETHER their phone may hold a token. Collapsing them would
    make it impossible to link an assignee for reporting without also
    handing them the field app."""
    branch = _branch()
    u = _user()
    d = _driver(branch)
    d.user_id = u.id
    db.session.commit()

    assert db.session.get(User, u.id).mobile_access is False


def test_license_expiry_column_still_works(app):
    """Guards against the new FK being added in a way that disturbs the
    existing Driver mapping."""
    d = _driver(_branch(), license_number="N01-23-456789",
                license_expiry=date(2027, 1, 1), license_type="PROFESSIONAL")
    assert db.session.get(Driver, d.id).license_expiry == date(2027, 1, 1)
