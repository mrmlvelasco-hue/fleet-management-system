"""VehicleAssignmentService -- the single writer.

Every path that changes who holds a vehicle goes through this, so the
history cannot disagree with the column. The tests that matter most are
the ones about what must NOT happen: no second open row, and no history
entry for an event that never occurred.
"""
from datetime import date

import pytest

from app.extensions import db
from app.modules.master_data.driver.models import Driver
from app.modules.master_data.org.models import Branch
from app.modules.master_data.reference.models import VehicleType
from app.modules.master_data.vehicle.assignment_models import VehicleAssignment
from app.modules.master_data.vehicle.assignment_service import (
    VehicleAssignmentService)
from app.modules.master_data.vehicle.models import Vehicle
from app.modules.master_data.vehicle.service import VehicleService


@pytest.fixture()
def env(app):
    b = Branch(code="CAR", name="Carmona")
    vt = VehicleType(code="LT", name="Light Truck", category="LIGHT")
    db.session.add_all([b, vt])
    db.session.commit()
    juan = Driver(person_id="PID-1", employee_number="EMP-001",
                  first_name="Juan", last_name="Cruz",
                  assignee_type="DRIVER", branch_id=b.id)
    maria = Driver(person_id="PID-2", employee_number="EMP-002",
                   first_name="Maria", last_name="Santos",
                   assignee_type="DRIVER", branch_id=b.id)
    db.session.add_all([juan, maria])
    db.session.commit()
    v1 = VehicleService().create(vehicle_type_id=vt.id, brand="Toyota",
                                 model="Hilux", year=2024, branch_id=b.id,
                                 conduction_number="A-001",
                                 plate_number="ABC-1234")
    v2 = VehicleService().create(vehicle_type_id=vt.id, brand="Isuzu",
                                 model="DMax", year=2023, branch_id=b.id,
                                 conduction_number="A-002",
                                 plate_number="XYZ-9876")
    db.session.commit()
    return juan, maria, v1, v2


def _open_rows(vehicle_id):
    return (VehicleAssignment.query
            .filter_by(vehicle_id=vehicle_id, assigned_to=None).all())


def test_assign_opens_a_row_and_syncs_the_column(app, env):
    juan, _maria, v1, _v2 = env

    VehicleAssignmentService().assign(v1.id, juan.id, source="MANUAL")

    assert len(_open_rows(v1.id)) == 1
    # The column is the cache the rest of the system reads. If it does
    # not move with the history, every existing screen is now lying.
    assert db.session.get(Vehicle, v1.id).assigned_driver_id == juan.id


def test_reassignment_closes_the_previous_row(app, env):
    juan, maria, v1, _v2 = env
    svc = VehicleAssignmentService()
    svc.assign(v1.id, juan.id, source="MANUAL")

    svc.assign(v1.id, maria.id, source="ATD",
               source_table="authority_to_drives", source_id=7)

    open_rows = _open_rows(v1.id)
    assert len(open_rows) == 1
    assert open_rows[0].driver_id == maria.id
    assert db.session.get(Vehicle, v1.id).assigned_driver_id == maria.id


def test_the_previous_assignment_survives_as_history(app, env):
    """The entire point of the table. Before this, reassigning
    overwrote the only evidence the earlier assignment existed."""
    juan, maria, v1, _v2 = env
    svc = VehicleAssignmentService()
    svc.assign(v1.id, juan.id, source="MANUAL")
    svc.assign(v1.id, maria.id, source="MANUAL")

    history = svc.history_for_vehicle(v1.id)

    assert len(history) == 2
    closed = [a for a in history if a.assigned_to is not None]
    assert len(closed) == 1
    assert closed[0].driver_id == juan.id


def test_reassigning_to_the_same_driver_is_a_no_op(app, env):
    """Saving the Vehicle form twice must not close and reopen an
    assignment. An audit table that records the act of pressing Save,
    rather than an actual handover, is worse than no audit table."""
    juan, _maria, v1, _v2 = env
    svc = VehicleAssignmentService()
    svc.assign(v1.id, juan.id, source="MANUAL")

    svc.assign(v1.id, juan.id, source="MANUAL")

    assert len(svc.history_for_vehicle(v1.id)) == 1
    assert len(_open_rows(v1.id)) == 1


def test_release_closes_the_row_and_clears_the_column(app, env):
    juan, _maria, v1, _v2 = env
    svc = VehicleAssignmentService()
    svc.assign(v1.id, juan.id, source="MANUAL")

    svc.release(v1.id, source="MANUAL")

    assert _open_rows(v1.id) == []
    assert db.session.get(Vehicle, v1.id).assigned_driver_id is None


def test_release_on_an_unassigned_vehicle_is_harmless(app, env):
    _juan, _maria, v1, _v2 = env
    VehicleAssignmentService().release(v1.id, source="MANUAL")
    assert _open_rows(v1.id) == []


def test_the_originating_document_is_recorded(app, env):
    juan, _maria, v1, _v2 = env

    VehicleAssignmentService().assign(
        v1.id, juan.id, source="ATD",
        source_table="authority_to_drives", source_id=42)

    row = _open_rows(v1.id)[0]
    assert row.source == "ATD"
    assert row.source_table == "authority_to_drives"
    assert row.source_id == 42


def test_one_driver_may_hold_several_vehicles(app, env):
    """Confirmed as intended: already true in practice, since
    _assigned_vehicles() returns a list. So Phase 2's endpoint is
    /my/vehicles, plural."""
    juan, _maria, v1, v2 = env
    svc = VehicleAssignmentService()

    svc.assign(v1.id, juan.id, source="MANUAL")
    svc.assign(v2.id, juan.id, source="MANUAL")

    assert {a.vehicle_id for a in svc.current_for_driver(juan.id)} == {
        v1.id, v2.id}


def test_exactly_one_open_row_after_a_chain_of_reassignments(app, env):
    juan, maria, v1, _v2 = env
    svc = VehicleAssignmentService()

    for driver in (juan, maria, juan, maria, juan):
        svc.assign(v1.id, driver.id, source="MANUAL")

    assert len(_open_rows(v1.id)) == 1
    assert len(svc.history_for_vehicle(v1.id)) == 5
    assert db.session.get(Vehicle, v1.id).assigned_driver_id == juan.id


def test_current_for_vehicle_returns_none_when_unassigned(app, env):
    _juan, _maria, v1, _v2 = env
    assert VehicleAssignmentService().current_for_vehicle(v1.id) is None


def test_assigned_from_defaults_to_today(app, env):
    juan, _maria, v1, _v2 = env
    VehicleAssignmentService().assign(v1.id, juan.id, source="MANUAL")
    assert _open_rows(v1.id)[0].assigned_from == date.today()
