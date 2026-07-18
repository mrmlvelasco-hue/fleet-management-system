from datetime import date

import pytest

from app.modules.master_data.driver.service import (
    DriverService, InvalidAssigneeError)
from app.modules.master_data.org.service import BranchService


@pytest.fixture()
def branch(db):
    return BranchService().create(code="BR-ASSIGNEE", name="Assignee Test Branch")


def test_driver_type_assignee_still_requires_license_fields(db, branch):
    """Backward compatibility: assignee_type=DRIVER (the default) keeps
    the exact original requirement -- license fields are mandatory."""
    with pytest.raises(InvalidAssigneeError):
        DriverService().create(
            employee_number="EMP-ASSIGNEE1", first_name="Juan", last_name="Dela Cruz",
            branch_id=branch.id, assignee_type="DRIVER")


def test_non_driver_assignee_does_not_require_license_fields(db, branch):
    """An Employee, Consultant, or Third Party Delivery assignee may be
    assigned a vehicle without personally holding a driver's license on
    file (e.g. a company car assigned to an executive with their own
    personal driver)."""
    person = DriverService().create(
        employee_number="EMP-ASSIGNEE2", first_name="Maria", last_name="Santos",
        branch_id=branch.id, assignee_type="EMPLOYEE")
    assert person.license_number is None
    assert person.assignee_type == "EMPLOYEE"


def test_person_id_auto_generated(db, branch):
    person = DriverService().create(
        employee_number="EMP-ASSIGNEE3", first_name="Test", last_name="Person",
        branch_id=branch.id, assignee_type="EMPLOYEE")
    assert person.person_id is not None
    assert person.person_id.startswith("PID-")


def test_extended_profile_fields_are_optional(db, branch):
    person = DriverService().create(
        employee_number="EMP-ASSIGNEE4", first_name="Minimal", last_name="Profile",
        branch_id=branch.id, assignee_type="EMPLOYEE")
    assert person.suffix is None
    assert person.nickname is None
    assert person.section is None
    assert person.position is None
    assert person.cost_center is None
    assert person.employment_status is None
    assert person.employment_type is None
    assert person.office_number is None
    assert person.home_number is None
    assert person.complete_address is None
    assert person.emergency_contact_person is None
    assert person.emergency_contact_number is None
    assert person.business_name is None


def test_full_profile_fields_can_be_set(db, branch):
    person = DriverService().create(
        employee_number="EMP-ASSIGNEE5", first_name="Full", last_name="Profile",
        branch_id=branch.id, assignee_type="CONSULTANT",
        suffix="Jr.", nickname="Full-Chan", section="IT Support",
        position="Senior Consultant", cost_center="CC-100",
        employment_status="ACTIVE", employment_type="CONTRACTUAL",
        office_number="02-8888-1234", home_number="02-8888-5678",
        complete_address="123 Main St, Manila",
        emergency_contact_person="Jane Doe", emergency_contact_number="0917-000-1111",
        business_name="Full Profile Consulting Inc.",
        business_contact_no="02-9999-0000", business_address="456 Business Ave")
    assert person.suffix == "Jr."
    assert person.nickname == "Full-Chan"
    assert person.section == "IT Support"
    assert person.position == "Senior Consultant"
    assert person.cost_center == "CC-100"
    assert person.employment_status == "ACTIVE"
    assert person.employment_type == "CONTRACTUAL"
    assert person.office_number == "02-8888-1234"
    assert person.home_number == "02-8888-5678"
    assert person.complete_address == "123 Main St, Manila"
    assert person.emergency_contact_person == "Jane Doe"
    assert person.emergency_contact_number == "0917-000-1111"
    assert person.business_name == "Full Profile Consulting Inc."


def test_existing_driver_creation_pattern_still_works_unchanged(db, branch):
    """Zero-regression check: every existing caller creates a driver
    exactly like before (assignee_type defaults to DRIVER, license
    fields still required) with no changes needed to that code path."""
    person = DriverService().create(
        employee_number="EMP-ASSIGNEE6", first_name="Legacy", last_name="Caller",
        license_number="LIC-ASSIGNEE6", license_expiry=date(2030, 1, 1),
        license_type="PROFESSIONAL", branch_id=branch.id)
    assert person.assignee_type == "DRIVER"
    assert person.license_number == "LIC-ASSIGNEE6"
