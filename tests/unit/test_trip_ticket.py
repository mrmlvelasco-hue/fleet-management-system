from datetime import datetime, date
from tests.conftest import tiny_jpeg_file

import pytest

from app.extensions import db as _db
from app.modules.transactions.trip_ticket.service import (
    TripTicketService, DriverRequiredError)
from app.modules.transactions.trip_ticket.models import TripTicket
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.driver.service import DriverService
from app.modules.document_config.service import (
    DocumentTypeService, NumberingSchemeService)
from app.modules.system_admin.services.system_parameter_service import (
    SystemParameterService)
from app.modules.system_admin.models import SystemParameter
from app.modules.user_management.models import User


def _add_active_atd(vehicle, driver):
    """Release now requires an approved, active, in-date ATD for the
    driver/vehicle pair (test_tripticket_odometer_sync.py established
    this same fixture pattern for the same new rule)."""
    from app.core.approval.models import ApprovalInstance
    from app.modules.document_config.models import DocumentType
    from app.modules.transactions.atd.models import AuthorityToDrive
    dt = DocumentType.query.filter_by(code="ATD").first()
    if dt is None:
        dt = DocumentTypeService().create(
            code="ATD", name="Authority To Drive",
            requires_approval=False, auto_numbering=True)
    atd = AuthorityToDrive(
        vehicle_id=vehicle.id, driver_id=driver.id,
        purpose="Trip Ticket regression fixture", valid_from=date.today(),
        valid_to=date.today(), status="ACTIVE")
    _db.session.add(atd)
    _db.session.flush()
    approval = ApprovalInstance(
        document_type_id=dt.id, reference_table="authority_to_drives",
        reference_id=atd.id, status="APPROVED")
    _db.session.add(approval)
    _db.session.flush()
    atd.approval_instance_id = approval.id
    _db.session.commit()


@pytest.fixture()
def env(db):
    branch = BranchService().create(code="BR-TT", name="Trip Branch")
    vt = VehicleTypeService().create(code="LV-TT", name="Light", category="LIGHT")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hiace", year=2024,
        branch_id=branch.id, conduction_number="TT-000")
    driver = DriverService().create(
        employee_number="EMP-TT1", first_name="Juan", last_name="Cruz",
        license_number="LIC-TT1", license_expiry=date(2030, 1, 1),
        license_type="PROFESSIONAL", branch_id=branch.id, photo_file=tiny_jpeg_file())
    user = User(username="requester1", email="req1@x.com", password_hash="x")
    db.session.add(user)
    db.session.add(SystemParameter(code="REQUIRE_DRIVER_FROM_MASTER",
                                   value="YES", data_type="STRING",
                                   group_name="TRIP_TICKET"))
    db.session.commit()

    dt = DocumentTypeService().create(code="TT", name="Trip Ticket",
                                      requires_approval=False,
                                      auto_numbering=True)
    NumberingSchemeService().create(document_type_id=dt.id, prefix="TT",
                                    include_year=True, digit_count=6,
                                    reset_policy="YEARLY")
    return vehicle, driver, user


def test_create_with_driver_from_master(db, env):
    vehicle, driver, user = env
    svc = TripTicketService()
    tt = svc.create(
        vehicle_id=vehicle.id, driver_id=driver.id,
        destination="Batangas", purpose="Delivery",
        departure_datetime=datetime(2026, 7, 15, 8, 0),
        odometer_out=1000, user=user)
    assert tt.document_number.startswith("TT-")
    assert tt.driver_id == driver.id
    assert tt.status == "DRAFT"


def test_create_without_driver_when_required_raises(db, env):
    vehicle, driver, user = env
    with pytest.raises(DriverRequiredError):
        TripTicketService().create(
            vehicle_id=vehicle.id, driver_name_manual="Manual Driver",
            destination="Batangas", purpose="Delivery",
            departure_datetime=datetime(2026, 7, 15, 8, 0),
            odometer_out=1000, user=user)


def test_manual_driver_allowed_when_parameter_no(db, env):
    vehicle, driver, user = env
    SystemParameterService().set("REQUIRE_DRIVER_FROM_MASTER", "NO")
    tt = TripTicketService().create(
        vehicle_id=vehicle.id, driver_name_manual="Manual Driver",
        destination="Batangas", purpose="Delivery",
        departure_datetime=datetime(2026, 7, 15, 8, 0),
        odometer_out=1000, user=user)
    assert tt.driver_name_manual == "Manual Driver"
    assert tt.driver_id is None


def test_submit_without_approval_requirement_auto_approves(db, env):
    vehicle, driver, user = env
    svc = TripTicketService()
    tt = svc.create(
        vehicle_id=vehicle.id, driver_id=driver.id,
        destination="Batangas", purpose="Delivery",
        departure_datetime=datetime(2026, 7, 15, 8, 0),
        odometer_out=1000, user=user)
    svc.submit(tt.id, user=user)
    refreshed = db.session.get(TripTicket, tt.id)
    assert refreshed.approval_instance_id is not None
    assert refreshed.approval_instance.status == "APPROVED"


def test_release_and_complete_trip(db, env):
    vehicle, driver, user = env
    svc = TripTicketService()
    tt = svc.create(
        vehicle_id=vehicle.id, driver_id=driver.id,
        destination="Batangas", purpose="Delivery",
        departure_datetime=datetime(2026, 7, 15, 8, 0),
        odometer_out=1000, user=user)
    svc.submit(tt.id, user=user)
    _add_active_atd(vehicle, driver)
    svc.release(tt.id)
    assert db.session.get(TripTicket, tt.id).status == "RELEASED"
    svc.complete(tt.id, odometer_in=1150,
                return_datetime=datetime(2026, 7, 15, 17, 0))
    completed = db.session.get(TripTicket, tt.id)
    assert completed.status == "COMPLETED"
    assert completed.odometer_in == 1150


def test_cancel_trip(db, env):
    vehicle, driver, user = env
    svc = TripTicketService()
    tt = svc.create(
        vehicle_id=vehicle.id, driver_id=driver.id,
        destination="Batangas", purpose="Delivery",
        departure_datetime=datetime(2026, 7, 15, 8, 0),
        odometer_out=1000, user=user)
    svc.cancel(tt.id, user=user)
    assert db.session.get(TripTicket, tt.id).status == "CANCELLED"
