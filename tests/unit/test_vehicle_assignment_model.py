"""The assignment history table.

`assigned_to IS NULL` is the definition of "currently held". Asserted
here rather than assumed, because every scope decision in Phase 2 will
be built on that predicate.
"""
from datetime import date

import pytest

from app.extensions import db
from app.modules.master_data.driver.models import Driver
from app.modules.master_data.org.models import Branch
from app.modules.master_data.reference.models import VehicleType
from app.modules.master_data.vehicle.assignment_models import VehicleAssignment
from app.modules.master_data.vehicle.models import Vehicle


@pytest.fixture()
def fixtures(app):
    b = Branch(code="CAR", name="Carmona")
    db.session.add(b)
    db.session.commit()
    d = Driver(person_id="PID-2026-000001", employee_number="EMP-001",
               first_name="Juan", last_name="Dela Cruz",
               assignee_type="DRIVER", branch_id=b.id)
    vt = VehicleType(code="LT", name="Light Truck", category="LIGHT")
    db.session.add(vt)
    db.session.commit()
    db.session.add(d)
    db.session.commit()
    from app.modules.master_data.vehicle.service import VehicleService
    v = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2024,
        branch_id=b.id, conduction_number="A-001", plate_number="ABC-1234")
    db.session.commit()
    return b, d, v


def test_an_open_assignment_has_no_end_date(app, fixtures):
    _b, d, v = fixtures
    a = VehicleAssignment(vehicle_id=v.id, driver_id=d.id,
                          assigned_from=date(2026, 9, 1), source="MANUAL")
    db.session.add(a)
    db.session.commit()

    assert db.session.get(VehicleAssignment, a.id).assigned_to is None


def test_a_closed_assignment_carries_an_end_date(app, fixtures):
    _b, d, v = fixtures
    a = VehicleAssignment(vehicle_id=v.id, driver_id=d.id,
                          assigned_from=date(2026, 1, 1),
                          assigned_to=date(2026, 9, 1), source="ATD")
    db.session.add(a)
    db.session.commit()

    assert db.session.get(VehicleAssignment, a.id).assigned_to == date(2026, 9, 1)


def test_the_originating_document_is_recorded(app, fixtures):
    """The hooks already know whether an ATD or an MO caused a change.
    Keeping it is what makes this table answer 'why did this vehicle
    change hands' rather than only 'who holds it'."""
    _b, d, v = fixtures
    a = VehicleAssignment(vehicle_id=v.id, driver_id=d.id, source="ATD",
                          source_table="authority_to_drives", source_id=42)
    db.session.add(a)
    db.session.commit()

    fetched = db.session.get(VehicleAssignment, a.id)
    assert fetched.source == "ATD"
    assert fetched.source_table == "authority_to_drives"
    assert fetched.source_id == 42


def test_a_backfilled_row_may_have_no_start_date(app, fixtures):
    """NULL honestly says 'in force, start unknown'. Inventing a
    plausible date would put fiction into an audit table."""
    _b, d, v = fixtures
    a = VehicleAssignment(vehicle_id=v.id, driver_id=d.id,
                          source="BACKFILL")
    db.session.add(a)
    db.session.commit()

    assert db.session.get(VehicleAssignment, a.id).assigned_from is None


def test_relationships_resolve(app, fixtures):
    _b, d, v = fixtures
    a = VehicleAssignment(vehicle_id=v.id, driver_id=d.id, source="MANUAL")
    db.session.add(a)
    db.session.commit()

    fetched = db.session.get(VehicleAssignment, a.id)
    assert fetched.vehicle.plate_number == "ABC-1234"
    assert fetched.driver.employee_number == "EMP-001"
