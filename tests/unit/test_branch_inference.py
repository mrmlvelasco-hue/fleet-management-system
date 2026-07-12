from datetime import date, datetime

import pytest

from app.modules.transactions.trip_ticket.service import TripTicketService
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.driver.service import DriverService
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.org.service import BranchService
from app.modules.document_config.service import (
    DocumentTypeService, NumberingSchemeService)
from app.modules.user_management.models import User


@pytest.fixture()
def env(db):
    branch = BranchService().create(code="BR-INFER", name="Infer Branch")
    vt = VehicleTypeService().create(code="LV-INFER", name="Light", category="LIGHT")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2024,
        branch_id=branch.id, conduction_number="INFER-000")
    driver = DriverService().create(
        employee_number="EMP-INFER1", first_name="Ana", last_name="Reyes",
        license_number="LIC-INFER1", license_expiry=date(2030, 1, 1),
        license_type="PROFESSIONAL", branch_id=branch.id)
    user = User(username="infer_user", email="infer@x.com", password_hash="x")
    from app.extensions import db as _db
    _db.session.add(user)
    _db.session.commit()

    dt = DocumentTypeService().create(code="TT", name="Trip Ticket",
                                      requires_approval=True,
                                      auto_numbering=True)
    NumberingSchemeService().create(document_type_id=dt.id, prefix="TT",
                                    include_year=True, digit_count=6,
                                    reset_policy="YEARLY")

    from app.modules.approval_config.service import (
        ApprovalPathService, ApprovalMatrixService)
    from app.modules.user_management.models import Role
    role = Role(name="Trip Approver Infer")
    _db.session.add(role)
    _db.session.commit()
    path = ApprovalPathService().create(name="Infer Path", levels=[
        {"level_number": 1, "approver_type": "ROLE", "role_id": role.id}])
    ApprovalMatrixService().create(dt.id, path.id, min_amount=None, max_amount=None)

    return branch, vehicle, driver, user


def test_submit_infers_branch_id_from_vehicle(db, env):
    branch, vehicle, driver, user = env
    svc = TripTicketService()
    trip = svc.create(
        vehicle_id=vehicle.id, driver_id=driver.id, destination="Tagaytay",
        purpose="Site visit",
        departure_datetime=datetime(2026, 7, 20, 8, 0),
        odometer_out=5000, user=user)
    svc.submit(trip.id, user=user)
    assert trip.approval_instance.branch_id == branch.id
