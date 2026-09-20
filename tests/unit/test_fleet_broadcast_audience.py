"""One audience rule, not two.

`_user_can_receive_broadcast` (admin_ops.py) and `_resolve_recipients`
(FleetBroadcastService) each decided independently who belongs to a
broadcast's audience. They agreed on the ordinary cases, but
`_user_can_receive_broadcast` never checked `is_active` on the user or the
role -- so a deactivated account, or a role that was retired after the
broadcast published, could still see the notice in `/my/fleet-broadcasts`
even though `_resolve_recipients` would never have delivered a
notification to them in the first place.

These tests exercise `user_matches_broadcast_audience`, the single
implementation both call sites now delegate to.
"""
import pytest

from app.extensions import db
from app.core.security.password import hash_password
from app.modules.system_admin.services.fleet_broadcast_service import (
    FleetBroadcastService)
from app.modules.user_management.models import Role, User


@pytest.fixture()
def svc():
    return FleetBroadcastService()


def _user(username, **kw):
    defaults = {"is_active": True}
    defaults.update(kw)
    u = User(username=username, email=f"{username}@e.com",
             password_hash=hash_password("secret123"), **defaults)
    db.session.add(u)
    return u


def _broadcast(admin, recipients):
    row = FleetBroadcastService().create(
        user=admin, broadcast_no=f"FB-AUD-{recipients[0]['recipient_type']}",
        broadcast_type="ANNOUNCEMENT", category="GENERAL",
        title="t", message="m", priority="NORMAL", recipients=recipients)
    return row


def test_all_users_matches_any_active_user(app, svc):
    admin = _user("audadm")
    driver = _user("auddrv")
    db.session.commit()
    row = _broadcast(admin, [{"recipient_type": "ALL_USERS"}])

    assert svc.user_matches_broadcast_audience(row, driver) is True


def test_all_users_does_not_match_a_deactivated_user(app, svc):
    """The one case the old admin_ops rule got wrong: it never looked at
    is_active at all, so a deactivated account still saw everything."""
    admin = _user("audadm2")
    driver = _user("auddrv2", is_active=False)
    db.session.commit()
    row = _broadcast(admin, [{"recipient_type": "ALL_USERS"}])

    assert svc.user_matches_broadcast_audience(row, driver) is False


def test_role_matches_a_user_holding_that_role(app, svc):
    admin = _user("audadm3")
    driver = _user("auddrv3")
    role = Role(name="Fleet Officer", is_system_role=False)
    db.session.add(role)
    db.session.flush()
    driver.roles.append(role)
    db.session.commit()
    row = _broadcast(admin, [{"recipient_type": "ROLE", "role_id": role.id}])

    assert svc.user_matches_broadcast_audience(row, driver) is True


def test_role_does_not_match_a_deactivated_role(app, svc):
    admin = _user("audadm4")
    driver = _user("auddrv4")
    role = Role(name="Retired Role", is_system_role=False, is_active=False)
    db.session.add(role)
    db.session.flush()
    driver.roles.append(role)
    db.session.commit()
    row = _broadcast(admin, [{"recipient_type": "ROLE", "role_id": role.id}])

    assert svc.user_matches_broadcast_audience(row, driver) is False


def test_branch_matches_a_user_in_that_branch(app, svc):
    admin = _user("audadm5")
    driver = _user("auddrv5", branch_id=7)
    db.session.commit()
    row = _broadcast(admin, [{"recipient_type": "BRANCH", "branch_id": 7}])

    assert svc.user_matches_broadcast_audience(row, driver) is True


def test_branch_does_not_match_a_user_in_a_different_branch(app, svc):
    admin = _user("audadm6")
    driver = _user("auddrv6", branch_id=9)
    db.session.commit()
    row = _broadcast(admin, [{"recipient_type": "BRANCH", "branch_id": 7}])

    assert svc.user_matches_broadcast_audience(row, driver) is False


def test_department_matches_a_user_in_that_department(app, svc):
    admin = _user("audadm7")
    driver = _user("auddrv7", department_id=3)
    db.session.commit()
    row = _broadcast(
        admin, [{"recipient_type": "DEPARTMENT", "department_id": 3}])

    assert svc.user_matches_broadcast_audience(row, driver) is True


def test_specific_user_matches_only_the_named_user(app, svc):
    admin = _user("audadm8")
    driver = _user("auddrv8")
    bystander = _user("audbys8")
    db.session.commit()
    row = _broadcast(
        admin, [{"recipient_type": "SPECIFIC_USER", "user_id": driver.id}])

    assert svc.user_matches_broadcast_audience(row, driver) is True
    assert svc.user_matches_broadcast_audience(row, bystander) is False
