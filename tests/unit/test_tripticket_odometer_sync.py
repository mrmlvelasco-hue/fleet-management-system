from datetime import date, datetime

import pytest

from app.core.security.password import hash_password
from app.modules.user_management.models import User, Role, Permission
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.driver.service import DriverService
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.org.service import BranchService
from app.modules.document_config.service import (
    DocumentTypeService, NumberingSchemeService)
from app.modules.transactions.trip_ticket.service import TripTicketService


@pytest.fixture()
def env(db):
    branch = BranchService().create(code="BR-ODOFIX", name="Odo Fix Branch")
    vt = VehicleTypeService().create(code="LV-ODOFIX", name="Light", category="LIGHT")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2024,
        branch_id=branch.id, conduction_number="ODOFIX-000",
        current_odometer=1000)
    driver = DriverService().create(
        employee_number="EMP-ODOFIX1", first_name="Ana", last_name="Cruz",
        license_number="LIC-ODOFIX1", license_expiry=date(2030, 1, 1),
        license_type="PROFESSIONAL", branch_id=branch.id)
    DocumentTypeService().create(code="TT", name="Trip Ticket",
                                 requires_approval=False, auto_numbering=True)
    from app.modules.document_config.models import DocumentType
    dt = DocumentType.query.filter_by(code="TT").first()
    NumberingSchemeService().create(document_type_id=dt.id, prefix="TT",
                                    include_year=True, digit_count=6,
                                    reset_policy="YEARLY")
    return branch, vt, vehicle, driver


def test_completing_trip_ticket_updates_vehicle_odometer(db, env):
    branch, vt, vehicle, driver = env
    trip = TripTicketService().create(
        vehicle_id=vehicle.id, driver_id=driver.id, destination="Tagaytay",
        purpose="Delivery", departure_datetime=datetime(2026, 7, 20, 8, 0),
        odometer_out=1000, user=None)
    TripTicketService().release(trip.id)
    TripTicketService().complete(trip.id, odometer_in=1250,
                                 return_datetime=datetime(2026, 7, 20, 17, 0))

    updated_vehicle = VehicleService().get(vehicle.id)
    assert updated_vehicle.current_odometer == 1250


def test_completing_trip_ticket_does_not_regress_odometer(db, env):
    """If the vehicle's current odometer is already higher (e.g. updated
    by a more recent Maintenance Order), a trip ticket with a stale/lower
    reading should never move it backwards."""
    branch, vt, vehicle, driver = env
    vehicle.current_odometer = 5000
    from app.extensions import db as _db
    _db.session.commit()

    trip = TripTicketService().create(
        vehicle_id=vehicle.id, driver_id=driver.id, destination="Tagaytay",
        purpose="Delivery", departure_datetime=datetime(2026, 7, 20, 8, 0),
        odometer_out=1000, user=None)
    TripTicketService().release(trip.id)
    TripTicketService().complete(trip.id, odometer_in=1250,
                                 return_datetime=datetime(2026, 7, 20, 17, 0))

    updated_vehicle = VehicleService().get(vehicle.id)
    assert updated_vehicle.current_odometer == 5000  # unchanged, not regressed
