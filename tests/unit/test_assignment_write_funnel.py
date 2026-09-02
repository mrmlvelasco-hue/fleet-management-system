"""Every writer of Vehicle.assigned_driver_id funnels through
VehicleAssignmentService.

This is what the one-open-row invariant depends on. It is enforced in
the service rather than by a database constraint (MySQL has no filtered
indexes, and the schema must stay migratable to SQL Server), so a writer
that bypasses the service would break the invariant silently -- the
column would move while the history stood still, and the two would
disagree with no error anywhere.

The most important test here is the one asserting a change writes
NOTHING: an unrelated edit to a vehicle must not record a handover that
never happened.
"""
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
    return b, vt, juan, maria


def _rows(vehicle_id):
    return VehicleAssignment.query.filter_by(vehicle_id=vehicle_id).all()


def _open(vehicle_id):
    return VehicleAssignment.query.filter_by(
        vehicle_id=vehicle_id, assigned_to=None).all()


# ── VehicleService.create ────────────────────────────────────────────

def test_create_with_an_assignee_opens_an_assignment(app, env):
    b, vt, juan, _maria = env

    v = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2024,
        branch_id=b.id, conduction_number="A-001", plate_number="ABC-1234",
        assigned_driver_id=juan.id)

    assert len(_open(v.id)) == 1
    assert _open(v.id)[0].driver_id == juan.id
    assert _open(v.id)[0].source == "MANUAL"
    assert db.session.get(Vehicle, v.id).assigned_driver_id == juan.id


def test_create_without_an_assignee_writes_no_row(app, env):
    b, vt, _juan, _maria = env

    v = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2024,
        branch_id=b.id, conduction_number="A-002", plate_number="ABC-0002")

    assert _rows(v.id) == []


# ── VehicleService.update ────────────────────────────────────────────

def test_update_reassignment_closes_the_old_row(app, env):
    b, vt, juan, maria = env
    v = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2024,
        branch_id=b.id, conduction_number="A-003", plate_number="ABC-0003",
        assigned_driver_id=juan.id)

    VehicleService().update(v.id, assigned_driver_id=maria.id)

    assert len(_open(v.id)) == 1
    assert _open(v.id)[0].driver_id == maria.id
    assert len(_rows(v.id)) == 2


def test_an_unrelated_edit_writes_NO_new_assignment(app, env):
    """The one that matters.

    The vehicle form always POSTs assigned_driver_id, so every edit --
    changing the odometer, fixing a typo in the model name -- arrives
    here carrying the SAME driver. If that opened a new row, the history
    would fill with handovers that never happened and become useless as
    an audit trail."""
    b, vt, juan, _maria = env
    v = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2024,
        branch_id=b.id, conduction_number="A-004", plate_number="ABC-0004",
        assigned_driver_id=juan.id)

    VehicleService().update(v.id, model="Hilux 4x4",
                            assigned_driver_id=juan.id)

    assert len(_rows(v.id)) == 1
    assert len(_open(v.id)) == 1


def test_update_that_omits_the_field_leaves_the_assignment_alone(app, env):
    """A partial update must not release a vehicle as a side effect.
    Absent means 'not supplied', not 'set to nobody'."""
    b, vt, juan, _maria = env
    v = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2024,
        branch_id=b.id, conduction_number="A-005", plate_number="ABC-0005",
        assigned_driver_id=juan.id)

    VehicleService().update(v.id, current_odometer=12345)

    assert len(_open(v.id)) == 1
    assert db.session.get(Vehicle, v.id).assigned_driver_id == juan.id


def test_update_to_none_releases_the_vehicle(app, env):
    b, vt, juan, _maria = env
    v = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2024,
        branch_id=b.id, conduction_number="A-006", plate_number="ABC-0006",
        assigned_driver_id=juan.id)

    VehicleService().update(v.id, assigned_driver_id=None)

    assert _open(v.id) == []
    assert db.session.get(Vehicle, v.id).assigned_driver_id is None
    # The period Juan held it survives.
    assert len(_rows(v.id)) == 1


# ── VehicleService.assign_driver ─────────────────────────────────────

def test_assign_driver_funnels_through_the_service(app, env):
    b, vt, juan, _maria = env
    v = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2024,
        branch_id=b.id, conduction_number="A-007", plate_number="ABC-0007")

    VehicleService().assign_driver(v.id, juan.id)

    assert len(_open(v.id)) == 1
    assert db.session.get(Vehicle, v.id).assigned_driver_id == juan.id


# ── assignment_hooks (ATD / MO) ──────────────────────────────────────

def test_hook_records_the_atd_as_the_source(app, env):
    b, vt, juan, _maria = env
    from app.modules.master_data.vehicle.assignment_hooks import (
        assign_driver_to_vehicle)
    v = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2024,
        branch_id=b.id, conduction_number="A-008", plate_number="ABC-0008")

    assign_driver_to_vehicle(v.id, juan.id, source="ATD",
                             source_table="authority_to_drives",
                             source_id=99)

    row = _open(v.id)[0]
    assert row.source == "ATD"
    assert row.source_table == "authority_to_drives"
    assert row.source_id == 99


def test_hook_defaults_keep_existing_callers_working(app, env):
    """The hook's signature gains optional arguments only. A caller that
    passes just the two ids -- as MO completion does today -- must keep
    working unchanged."""
    b, vt, juan, _maria = env
    from app.modules.master_data.vehicle.assignment_hooks import (
        assign_driver_to_vehicle)
    v = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2024,
        branch_id=b.id, conduction_number="A-009", plate_number="ABC-0009")

    assign_driver_to_vehicle(v.id, juan.id)

    assert len(_open(v.id)) == 1
    assert db.session.get(Vehicle, v.id).assigned_driver_id == juan.id


def test_hook_still_no_ops_on_missing_ids(app, env):
    """Unchanged safety property: a failed lookup here must never block
    an ATD approval or MO completion from going through."""
    from app.modules.master_data.vehicle.assignment_hooks import (
        assign_driver_to_vehicle)
    assign_driver_to_vehicle(None, None)
    assign_driver_to_vehicle(999999, 999999)
    assert VehicleAssignment.query.count() == 0
