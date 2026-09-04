"""Default roles for the field app.

Written because a real device showed the Checklists tab missing: the
account's hand-built role held vehicle.view and atd.view but not
checklist.view, so the tab correctly hid itself and the driver had no
way to file an inspection.

Only "System Administrator" was ever seeded. Every other role was built
by hand, which means the permission set the mobile app actually needs
lived in somebody's memory rather than in the system.
"""
import pytest

from app.extensions import db
from app.modules.user_management.models import Permission, Role
from app.cli import _seed_field_roles
from app.core.security.registry import sync_permissions

ASSIGNEE = "Vehicle Assignee"
REVIEWER = "Fleet Officer"


@pytest.fixture()
def seeded(app):
    sync_permissions()
    db.session.commit()
    _seed_field_roles()
    db.session.commit()


def _codes(name):
    role = Role.query.filter_by(name=name).first()
    assert role is not None, f"{name} was not seeded"
    return {p.code for p in role.permissions}


def test_vehicle_assignee_role_is_seeded(app, seeded):
    assert Role.query.filter_by(name=ASSIGNEE).first() is not None


def test_assignee_can_see_every_field_app_tab(app, seeded):
    """The bug that prompted this. Each of these maps to a tab; a
    missing one hides the tab silently rather than erroring, so the
    driver simply cannot reach the screen."""
    codes = _codes(ASSIGNEE)
    for code in ("vehicle.view", "checklist.view", "atd.view"):
        assert code in codes, f"{code} missing -- its tab will not appear"


def test_assignee_can_do_the_work(app, seeded):
    codes = _codes(ASSIGNEE)
    assert "vehicle.update" in codes      # odometer
    assert "checklist.create" in codes
    assert "checklist.update" in codes


def test_assignee_CAN_submit_their_own_inspection(app, seeded):
    """Reversed by the client on 2026-09-04.

    This test previously asserted the opposite: that a driver must not
    finalise their own inspection, because filling one and signing it
    off were two different people's jobs. The client has since decided
    the driver submits, and SUBMITTED means "final, ready for Fleet to
    review".

    Safe only because checklist.submit was SPLIT from checklist.review
    at the same time. Before the split this one code also widened list,
    detail, create and delete scope, so granting it here would have
    handed every driver the whole fleet's inspections -- silently.
    """
    assert "checklist.submit" in _codes(ASSIGNEE)


def test_assignee_cannot_REVIEW_everyone_elses(app, seeded):
    """The boundary that replaced it, and the one now doing the work.

    Reviewing other people's inspections stays a Fleet Officer's job.
    If this code ever appears in the Assignee role, every scoping guard
    in the checklist module opens at once.
    """
    assert "checklist.review" not in _codes(ASSIGNEE)


def test_assignee_holds_nothing_administrative(app, seeded):
    codes = _codes(ASSIGNEE)
    for code in ("user.view", "role.view", "mobileapp.create",
                 "driver.update", "vehicle.delete"):
        assert code not in codes


def test_fleet_officer_can_review(app, seeded):
    codes = _codes(REVIEWER)
    assert "checklist.review" in codes
    assert "checklist.submit" in codes
    assert "checklist.view" in codes


def test_seeding_twice_does_not_duplicate(app, seeded):
    _seed_field_roles()
    db.session.commit()
    assert Role.query.filter_by(name=ASSIGNEE).count() == 1


def test_seeding_never_overwrites_an_edited_role(app, seeded):
    """An administrator who tailors these roles must not have their
    work silently reverted the next time somebody runs seed. Seeding
    creates; it does not reconcile."""
    role = Role.query.filter_by(name=ASSIGNEE).first()
    extra = Permission.query.filter_by(code="tripticket.view").first()
    role.permissions.append(extra)
    db.session.commit()

    _seed_field_roles()
    db.session.commit()

    assert "tripticket.view" in _codes(ASSIGNEE)


def test_seeded_roles_are_not_system_roles(app, seeded):
    """System roles are protected from editing. These are starting
    points the client is expected to tailor, so they must stay
    editable."""
    assert Role.query.filter_by(name=ASSIGNEE).first().is_system_role is False
