"""Configurable creation constraints for Driver / Assignee records.

`DriverService.create` enforces two rules unconditionally:

  1. A DRIVER-type assignee must have licence number, expiry and type.
  2. EVERY new assignee must have a photo, because it prints on the
     Vehicle Assignment Memo and the Issuance / Receiving Checklist.

Both are correct for day-to-day use and both BLOCK the client's
migration from manual records. `create()` raises, so a legacy driver
with no licence on file cannot be entered at all -- the record has to be
falsified or left out, and a driver missing from the roster is worse
than one with an incomplete record.

So they become System Parameters, which is what the master prompt asks
for: "All business rules must be configurable through System
Administration whenever possible. No values shall be hardcoded."

Note this RELAXES existing behaviour rather than adding missing
validation. The defaults below (permissive) are the client's decision,
taken for the migration; they are expected to be switched on afterwards.

Enforced inside the SERVICE, not in the API layer, so the Jinja form and
the REST API inherit one behaviour. Enforcing in the API alone would
leave the two apps applying different rules to the same table.
"""
import io

import pytest

from app.modules.master_data.driver.service import (
    DriverService, InvalidAssigneeError)
from app.modules.master_data.org.service import BranchService
from app.modules.system_admin.models import SystemParameter
from app.modules.system_admin.services.system_parameter_service import (
    SystemParameterService)


def _photo():
    from werkzeug.datastructures import FileStorage
    return FileStorage(stream=io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"0" * 64),
                       filename="id.png", content_type="image/png")


def _param(db, code, value, group="DRIVER"):
    row = SystemParameter.query.filter_by(code=code).first()
    if row is None:
        row = SystemParameter(code=code, value=str(value),
                              data_type="BOOLEAN", group_name=group,
                              description=f"Migration switch: {code}",
                              is_active=True)
        db.session.add(row)
    else:
        row.value = str(value)
    db.session.commit()
    return row


@pytest.fixture()
def branch(db):
    b = BranchService().create(code="BR-PARAM", name="Param Branch")
    db.session.commit()
    return b


# ── Licence requirement ─────────────────────────────────────────────────────

def test_licence_is_required_by_default_when_the_parameter_is_on(db, branch):
    _param(db, "REQUIRE_DRIVER_LICENSE", "YES")
    with pytest.raises(InvalidAssigneeError):
        DriverService().create(
            employee_number="PRM-001", first_name="No", last_name="Licence",
            branch_id=branch.id, assignee_type="DRIVER", photo_file=_photo())


def test_licence_can_be_waived_for_migration(db, branch):
    """The point of the parameter. A legacy driver whose paper record
    has no licence on file must still be enterable, or the migration
    silently loses people."""
    _param(db, "REQUIRE_DRIVER_LICENSE", "NO")
    d = DriverService().create(
        employee_number="PRM-002", first_name="Legacy", last_name="Driver",
        branch_id=branch.id, assignee_type="DRIVER", photo_file=_photo())
    assert d.id is not None
    assert d.license_number is None


def test_waiving_the_licence_does_not_waive_anything_else(db, branch):
    """Relaxing one constraint must not relax the duplicate check --
    that one protects against two people holding the same licence
    number, which is a data-integrity problem, not a migration one."""
    from app.modules.master_data.driver.service import DuplicateDriverError

    _param(db, "REQUIRE_DRIVER_LICENSE", "NO")
    DriverService().create(
        employee_number="PRM-003", first_name="First", last_name="Holder",
        branch_id=branch.id, assignee_type="DRIVER",
        license_number="N-DUPE", license_expiry=None,
        license_type="PROFESSIONAL", photo_file=_photo())
    with pytest.raises(DuplicateDriverError):
        DriverService().create(
            employee_number="PRM-004", first_name="Second",
            last_name="Holder", branch_id=branch.id, assignee_type="DRIVER",
            license_number="N-DUPE", photo_file=_photo())


def test_a_non_driver_never_needed_a_licence(db, branch):
    """Unchanged behaviour: consultants and third-party delivery
    personnel can be assigned a vehicle without holding one
    personally."""
    _param(db, "REQUIRE_DRIVER_LICENSE", "YES")
    d = DriverService().create(
        employee_number="PRM-005", first_name="Ana", last_name="Consultant",
        branch_id=branch.id, assignee_type="CONSULTANT", photo_file=_photo())
    assert d.id is not None


def test_the_parameter_is_read_per_call_not_cached(db, branch):
    """Toggling it in System Administration must take effect
    immediately. Read once at import, it would need a restart -- and
    nobody would connect the two."""
    _param(db, "REQUIRE_DRIVER_LICENSE", "NO")
    DriverService().create(
        employee_number="PRM-006", first_name="Before", last_name="Toggle",
        branch_id=branch.id, assignee_type="DRIVER", photo_file=_photo())

    SystemParameterService().set("REQUIRE_DRIVER_LICENSE", "YES")
    with pytest.raises(InvalidAssigneeError):
        DriverService().create(
            employee_number="PRM-007", first_name="After",
            last_name="Toggle", branch_id=branch.id,
            assignee_type="DRIVER", photo_file=_photo())


def test_an_absent_parameter_keeps_the_original_behaviour(db, branch):
    """An install predating this feature must behave as it did before,
    not silently become permissive. The default is STRICT; the client's
    permissive setting is an explicit choice recorded in the database."""
    SystemParameter.query.filter_by(
        code="REQUIRE_DRIVER_LICENSE").delete()
    db.session.commit()
    with pytest.raises(InvalidAssigneeError):
        DriverService().create(
            employee_number="PRM-008", first_name="No", last_name="Param",
            branch_id=branch.id, assignee_type="DRIVER", photo_file=_photo())


# ── Photo requirement ───────────────────────────────────────────────────────

def test_photo_is_required_when_the_parameter_is_on(db, branch):
    _param(db, "REQUIRE_ASSIGNEE_PHOTO", "YES")
    with pytest.raises(InvalidAssigneeError):
        DriverService().create(
            employee_number="PRM-010", first_name="No", last_name="Photo",
            branch_id=branch.id, assignee_type="CONSULTANT")


def test_photo_can_be_waived_for_migration(db, branch):
    """Legacy paper records have no photograph. This blocks the
    migration exactly as hard as the licence rule and applies to EVERY
    assignee type, not just drivers."""
    _param(db, "REQUIRE_ASSIGNEE_PHOTO", "NO")
    d = DriverService().create(
        employee_number="PRM-011", first_name="Legacy", last_name="NoPhoto",
        branch_id=branch.id, assignee_type="CONSULTANT")
    assert d.id is not None
    assert d.photo_attachment_id is None


def test_a_photo_is_still_stored_when_one_is_supplied(db, branch):
    """Waiving the REQUIREMENT must not disable the feature. A record
    created with a photo during migration keeps it."""
    _param(db, "REQUIRE_ASSIGNEE_PHOTO", "NO")
    d = DriverService().create(
        employee_number="PRM-012", first_name="Has", last_name="Photo",
        branch_id=branch.id, assignee_type="CONSULTANT",
        photo_file=_photo())
    assert d.photo_attachment_id is not None


def test_an_absent_photo_parameter_keeps_the_original_behaviour(db, branch):
    SystemParameter.query.filter_by(
        code="REQUIRE_ASSIGNEE_PHOTO").delete()
    db.session.commit()
    with pytest.raises(InvalidAssigneeError):
        DriverService().create(
            employee_number="PRM-013", first_name="No", last_name="Param2",
            branch_id=branch.id, assignee_type="CONSULTANT")


def test_the_two_parameters_are_independent(db, branch):
    """Waiving the photo must not waive the licence. They exist for the
    same migration but describe different facts, and collapsing them
    would let one client decision silently make another."""
    _param(db, "REQUIRE_ASSIGNEE_PHOTO", "NO")
    _param(db, "REQUIRE_DRIVER_LICENSE", "YES")
    with pytest.raises(InvalidAssigneeError):
        DriverService().create(
            employee_number="PRM-014", first_name="Still",
            last_name="NeedsLicence", branch_id=branch.id,
            assignee_type="DRIVER")
