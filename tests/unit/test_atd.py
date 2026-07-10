from datetime import date

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
        license_type="PROFESSIONAL", branch_id=branch.id)
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
