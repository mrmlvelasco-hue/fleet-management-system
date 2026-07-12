from datetime import date, datetime

import pytest

from app.modules.transactions.vehicle_movement.service import (
    VehicleMovementService)
from app.modules.transactions.vehicle_movement.models import VehicleMovement
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.driver.service import DriverService
from app.modules.document_config.service import (
    DocumentTypeService, NumberingSchemeService)
from app.modules.user_management.models import User


@pytest.fixture()
def env(db):
    branch = BranchService().create(code="BR-VM", name="VM Branch")
    vt = VehicleTypeService().create(code="LV-VM", name="Light", category="LIGHT")
    driver = DriverService().create(
        employee_number="EMP-VM1", first_name="Rey", last_name="Torres",
        license_number="LIC-VM1", license_expiry=date(2030, 1, 1),
        license_type="PROFESSIONAL", branch_id=branch.id)
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hiace", year=2024,
        branch_id=branch.id, conduction_number="VM-000",
        assigned_driver_id=driver.id)
    user = User(username="vm_user", email="vm@x.com", password_hash="x")
    from app.extensions import db as _db
    _db.session.add(user)
    _db.session.commit()

    dt = DocumentTypeService().create(code="VM", name="Vehicle Movement",
                                      requires_approval=False,
                                      auto_numbering=True)
    NumberingSchemeService().create(document_type_id=dt.id, prefix="VM",
                                    include_year=True, digit_count=6,
                                    reset_policy="YEARLY")
    return branch, vehicle, driver, user


def test_create_defaults_driver_from_vehicle_assigned_driver(db, env):
    branch, vehicle, driver, user = env
    mv = VehicleMovementService().create(
        vehicle_id=vehicle.id, movement_type="TRANSFER",
        from_location="HQ", to_location="Branch B",
        movement_date=date(2026, 7, 15), user=user)
    assert mv.driver_id == driver.id


def test_create_allows_overriding_driver(db, env):
    branch, vehicle, driver, user = env
    other_driver = DriverService().create(
        employee_number="EMP-VM2", first_name="Ben", last_name="Reyes",
        license_number="LIC-VM2", license_expiry=date(2030, 1, 1),
        license_type="PROFESSIONAL", branch_id=branch.id)
    mv = VehicleMovementService().create(
        vehicle_id=vehicle.id, movement_type="TRANSFER",
        from_location="HQ", to_location="Branch B",
        movement_date=date(2026, 7, 15), user=user,
        driver_id=other_driver.id)
    assert mv.driver_id == other_driver.id


def test_create_with_employee_responsible_and_purpose(db, env):
    branch, vehicle, driver, user = env
    mv = VehicleMovementService().create(
        vehicle_id=vehicle.id, movement_type="TRANSFER",
        from_location="HQ", to_location="Branch B",
        movement_date=date(2026, 7, 15), user=user,
        employee_responsible="Supervisor Juan Dela Cruz",
        purpose="Relocate spare vehicle to Branch B",
        movement_start_datetime=datetime(2026, 7, 15, 8, 0))
    assert mv.employee_responsible == "Supervisor Juan Dela Cruz"
    assert mv.purpose == "Relocate spare vehicle to Branch B"
    assert mv.movement_start_datetime == datetime(2026, 7, 15, 8, 0)


def test_complete_sets_movement_end_datetime(db, env):
    branch, vehicle, driver, user = env
    mv = VehicleMovementService().create(
        vehicle_id=vehicle.id, movement_type="TRANSFER",
        from_location="HQ", to_location="Branch B",
        movement_date=date(2026, 7, 15), user=user)
    VehicleMovementService().start_transit(mv.id)
    VehicleMovementService().complete(
        mv.id, movement_end_datetime=datetime(2026, 7, 15, 17, 0))
    completed = db.session.get(VehicleMovement, mv.id)
    assert completed.status == "COMPLETED"
    assert completed.movement_end_datetime == datetime(2026, 7, 15, 17, 0)


def test_vehicle_with_no_assigned_driver_leaves_movement_driver_blank(db, env):
    branch, vehicle, driver, user = env
    vt2 = VehicleTypeService().create(code="LV-VM2", name="Light2", category="LIGHT")
    vehicle_no_driver = VehicleService().create(
        vehicle_type_id=vt2.id, brand="Honda", model="City", year=2024,
        branch_id=branch.id, conduction_number="VM-001")
    mv = VehicleMovementService().create(
        vehicle_id=vehicle_no_driver.id, movement_type="TRANSFER",
        from_location="HQ", to_location="Branch B",
        movement_date=date(2026, 7, 15), user=user)
    assert mv.driver_id is None
