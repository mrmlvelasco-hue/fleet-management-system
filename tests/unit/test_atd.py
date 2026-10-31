from datetime import date
from tests.conftest import tiny_jpeg_file

import pytest

from app.modules.transactions.atd.service import ATDService
from app.modules.transactions.atd.models import AuthorityToDrive
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.driver.service import DriverService
from app.modules.document_config.service import (
    DocumentTypeService, NumberingSchemeService)
from app.modules.approval_config.service import (
    ApprovalPathService, ApprovalMatrixService)
from app.modules.user_management.models import User, Role


@pytest.fixture()
def env(db):
    branch = BranchService().create(code="BR-ATD", name="ATD Branch")
    vt = VehicleTypeService().create(code="LV-ATD", name="Light", category="LIGHT")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2024,
        branch_id=branch.id, conduction_number="ATD-000")
    driver = DriverService().create(
        employee_number="EMP-ATD1", first_name="Maria", last_name="Santos",
        license_number="LIC-ATD1", license_expiry=date(2030, 1, 1),
        license_type="PROFESSIONAL", branch_id=branch.id, photo_file=tiny_jpeg_file())
    approver_role = Role(name="ATD Approver")
    approver = User(username="atd_approver", email="aa@x.com",
                    password_hash="x")
    approver.roles.append(approver_role)
    requester = User(username="atd_requester", email="ar@x.com",
                     password_hash="x")
    db.session.add_all([approver_role, approver, requester])
    db.session.commit()

    dt = DocumentTypeService().create(code="ATD", name="Authority To Drive",
                                      requires_approval=True,
                                      auto_numbering=True)
    NumberingSchemeService().create(document_type_id=dt.id, prefix="ATD",
                                    include_year=True, digit_count=6,
                                    reset_policy="YEARLY")
    path = ApprovalPathService().create(name="ATD Path", levels=[
        {"level_number": 1, "approver_type": "ROLE", "role_id": approver_role.id}])
    ApprovalMatrixService().create(dt.id, path.id)
    return vehicle, driver, requester, approver


def test_create_atd(db, env):
    vehicle, driver, requester, approver = env
    svc = ATDService()
    atd = svc.create(vehicle_id=vehicle.id, driver_id=driver.id,
                     purpose="Client visit", valid_from=date(2026, 7, 15),
                     valid_to=date(2026, 7, 31), user=requester)
    assert atd.document_number.startswith("ATD-")
    assert atd.status == "DRAFT"


def test_submit_and_approve_activates(db, env):
    vehicle, driver, requester, approver = env
    svc = ATDService()
    atd = svc.create(vehicle_id=vehicle.id, driver_id=driver.id,
                     purpose="Client visit", valid_from=date(2026, 7, 15),
                     valid_to=date(2026, 7, 31), user=requester)
    svc.submit(atd.id, user=requester)
    refreshed = db.session.get(AuthorityToDrive, atd.id)
    assert refreshed.approval_instance.status == "PENDING"
    svc.approve(atd.id, user=approver)
    svc.activate(atd.id)
    assert db.session.get(AuthorityToDrive, atd.id).status == "ACTIVE"


def test_activate_before_approval_raises(db, env):
    from app.modules.transactions.atd.service import InvalidATDStateError
    vehicle, driver, requester, approver = env
    svc = ATDService()
    atd = svc.create(vehicle_id=vehicle.id, driver_id=driver.id,
                     purpose="Client visit", valid_from=date(2026, 7, 15),
                     valid_to=date(2026, 7, 31), user=requester)
    svc.submit(atd.id, user=requester)
    with pytest.raises(InvalidATDStateError):
        svc.activate(atd.id)


def test_cancel_atd(db, env):
    vehicle, driver, requester, approver = env
    svc = ATDService()
    atd = svc.create(vehicle_id=vehicle.id, driver_id=driver.id,
                     purpose="Client visit", valid_from=date(2026, 7, 15),
                     valid_to=date(2026, 7, 31), user=requester)
    svc.cancel(atd.id, user=requester)
    assert db.session.get(AuthorityToDrive, atd.id).status == "CANCELLED"


def test_vehicle_type_authorization_needs_no_vehicle_or_odometer(db, env):
    vehicle, driver, requester, approver = env
    atd = ATDService().create(
        authorization_type="VEHICLE_TYPE", vehicle_category="LIGHT",
        vehicle_id=None, driver_id=driver.id, purpose="Pool vehicle use",
        valid_from=date.today(), valid_to=date.today(), odometer_out=123,
        user=requester)
    assert atd.vehicle_id is None
    assert atd.vehicle_type_id is None
    assert atd.vehicle_category == "LIGHT"
    assert atd.odometer_out is None


def test_motorcycle_only_needs_no_vehicle_or_type(db, env):
    vehicle, driver, requester, approver = env
    atd = ATDService().create(
        authorization_type="MOTORCYCLE_ONLY", vehicle_id=None,
        vehicle_type_id=None, driver_id=driver.id, purpose="Motorcycle use",
        valid_from=date.today(), valid_to=date.today(), user=requester)
    assert atd.vehicle_id is None
    assert atd.vehicle_type_id is None


def test_specific_vehicle_without_vehicle_is_rejected(db, env):
    _vehicle, driver, requester, _approver = env
    from app.modules.transactions.atd.service import ATDValidationError
    with pytest.raises(ATDValidationError, match="vehicle is required"):
        ATDService().create(
            authorization_type="SPECIFIC_VEHICLE", vehicle_id=None,
            driver_id=driver.id, purpose="Specific use",
            valid_from=date.today(), valid_to=date.today(), user=requester)


def test_unknown_vehicle_category_is_rejected(db, env):
    _vehicle, driver, requester, _approver = env
    from app.modules.transactions.atd.service import ATDValidationError
    with pytest.raises(ATDValidationError, match="Vehicle Category"):
        ATDService().create(
            authorization_type="VEHICLE_TYPE", vehicle_category="NOT_A_LOOKUP",
            driver_id=driver.id, purpose="Invalid category",
            valid_from=date.today(), valid_to=date.today(), user=requester)


def test_type_authority_only_matches_same_vehicle_type_when_live(db, env):
    vehicle, driver, requester, approver = env
    atd = ATDService().create(
        authorization_type="VEHICLE_TYPE", vehicle_category="LIGHT",
        driver_id=driver.id, purpose="Pool vehicle use",
        valid_from=date.today(), valid_to=date.today(), user=requester)
    ATDService().submit(atd.id, requester)
    ATDService().approve(atd.id, approver)
    ATDService().activate(atd.id)
    assert ATDService.authorizes_vehicle(atd, vehicle)

    other_type = VehicleTypeService().create(
        code="OTHER-ATD", name="Other type", category="HEAVY")
    other_vehicle = VehicleService().create(
        vehicle_type_id=other_type.id, brand="Other", model="Unit",
        year=2024, conduction_number="ATD-OTHER")
    assert not ATDService.authorizes_vehicle(atd, other_vehicle)


def test_motorcycle_only_authorizes_motorcycles_and_requires_live_approval(db, env):
    vehicle, driver, requester, approver = env
    branch_id = vehicle.branch_id
    motorcycle_type = VehicleTypeService().create(
        code="MOTO-ATD", name="Motorcycle", category="MOTORCYCLE")
    motorcycle = VehicleService().create(
        vehicle_type_id=motorcycle_type.id, brand="Honda", model="Click",
        year=2024, branch_id=branch_id, conduction_number="ATD-MOTO")
    atd = ATDService().create(
        authorization_type="MOTORCYCLE_ONLY", driver_id=driver.id,
        purpose="Motorcycle use", valid_from=date.today(),
        valid_to=date.today(), user=requester)
    assert not ATDService.authorizes_vehicle(atd, motorcycle)
    ATDService().submit(atd.id, requester)
    ATDService().approve(atd.id, approver)
    ATDService().activate(atd.id)
    assert ATDService.authorizes_vehicle(atd, motorcycle)
    assert not ATDService.authorizes_vehicle(atd, vehicle)
