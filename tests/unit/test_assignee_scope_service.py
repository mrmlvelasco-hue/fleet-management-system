"""AssigneeScopeService -- "which vehicles are THIS person's".

Narrower than UserOrgScopeService on purpose, and it must stay narrower.
Org scope answers "which vehicles may this ROLE see", which for a Fleet
Officer is correctly the whole branch. This answers "which vehicles is
this PERSON responsible for", which for a driver is one or two.

Widening this into org scope is exactly finding J1 -- the defect Phase 2
exists to fix. The org-scope test below is the guard against
reintroducing it, and is mutation-checked.
"""
import pytest

from app.core.security.password import hash_password
from app.extensions import db
from app.modules.master_data.driver.models import Driver
from app.modules.master_data.driver.service import DriverService
from app.modules.master_data.org.models import Branch
from app.modules.master_data.reference.models import VehicleType
from app.modules.master_data.vehicle.assignment_service import (
    VehicleAssignmentService)
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.user_management.assignee_scope_service import (
    AssigneeScopeService)
from app.modules.user_management.models import User


@pytest.fixture()
def env(app):
    b = Branch(code="CAR", name="Carmona")
    vt = VehicleType(code="LT", name="Light Truck", category="LIGHT")
    db.session.add_all([b, vt])
    db.session.commit()

    juan_user = User(username="juan", email="juan@example.com",
                     password_hash=hash_password("secret123"),
                     is_active=True, branch_id=b.id)
    maria_user = User(username="maria", email="maria@example.com",
                      password_hash=hash_password("secret123"),
                      is_active=True, branch_id=b.id)
    db.session.add_all([juan_user, maria_user])
    db.session.commit()

    juan = Driver(person_id="PID-1", employee_number="EMP-001",
                  first_name="Juan", last_name="Cruz",
                  assignee_type="DRIVER", branch_id=b.id)
    maria = Driver(person_id="PID-2", employee_number="EMP-002",
                   first_name="Maria", last_name="Santos",
                   assignee_type="DRIVER", branch_id=b.id)
    db.session.add_all([juan, maria])
    db.session.commit()
    DriverService().link_user(juan.id, juan_user.id)
    DriverService().link_user(maria.id, maria_user.id)

    v1 = VehicleService().create(vehicle_type_id=vt.id, brand="Toyota",
                                 model="Hilux", year=2024, branch_id=b.id,
                                 conduction_number="A-001",
                                 plate_number="ABC-1234")
    v2 = VehicleService().create(vehicle_type_id=vt.id, brand="Isuzu",
                                 model="DMax", year=2023, branch_id=b.id,
                                 conduction_number="A-002",
                                 plate_number="XYZ-9876")
    db.session.commit()
    return juan_user, maria_user, juan, maria, v1, v2


def test_assignee_for_resolves_the_linked_driver(app, env):
    juan_user, _mu, juan, _m, _v1, _v2 = env
    assert AssigneeScopeService().assignee_for(juan_user).id == juan.id


def test_assignee_for_is_none_when_unlinked(app, env):
    _ju, _mu, _j, _m, _v1, _v2 = env
    stranger = User(username="stranger", email="s@example.com",
                    password_hash=hash_password("x"), is_active=True)
    db.session.add(stranger)
    db.session.commit()
    assert AssigneeScopeService().assignee_for(stranger) is None


def test_unlinked_user_is_assigned_nothing(app, env):
    _ju, _mu, _j, _m, _v1, _v2 = env
    stranger = User(username="stranger", email="s@example.com",
                    password_hash=hash_password("x"), is_active=True)
    db.session.add(stranger)
    db.session.commit()
    assert AssigneeScopeService().assigned_vehicle_ids(stranger) == []


def test_assigned_vehicle_ids_returns_their_vehicles(app, env):
    juan_user, _mu, juan, _m, v1, v2 = env
    svc = VehicleAssignmentService()
    svc.assign(v1.id, juan.id, source="MANUAL")
    svc.assign(v2.id, juan.id, source="MANUAL")

    assert set(AssigneeScopeService().assigned_vehicle_ids(juan_user)) == {
        v1.id, v2.id}


def test_a_closed_assignment_is_excluded(app, env):
    juan_user, _mu, juan, _m, v1, _v2 = env
    svc = VehicleAssignmentService()
    svc.assign(v1.id, juan.id, source="MANUAL")
    svc.release(v1.id, source="MANUAL")

    assert AssigneeScopeService().assigned_vehicle_ids(juan_user) == []


def test_covers_vehicle_true_for_their_own(app, env):
    juan_user, _mu, juan, _m, v1, _v2 = env
    VehicleAssignmentService().assign(v1.id, juan.id, source="MANUAL")

    assert AssigneeScopeService().covers_vehicle(juan_user, v1.id) is True


def test_covers_vehicle_false_for_someone_elses(app, env):
    juan_user, _mu, _j, maria, v1, _v2 = env
    VehicleAssignmentService().assign(v1.id, maria.id, source="MANUAL")

    assert AssigneeScopeService().covers_vehicle(juan_user, v1.id) is False


def test_covers_vehicle_does_NOT_fall_back_to_org_scope(app, env):
    """THE guard.

    Juan and Maria share a branch, so org scope covers both vehicles for
    either of them -- and that is correct for a Fleet Officer. It is not
    correct for a driver. If this service ever widens into org scope,
    the field app hands every driver the whole branch, which is finding
    J1 reintroduced through the very service written to prevent it.
    """
    juan_user, _mu, _j, maria, v1, _v2 = env
    VehicleAssignmentService().assign(v1.id, maria.id, source="MANUAL")

    # Same branch as the vehicle -- org scope would say yes.
    assert juan_user.branch_id == db.session.get(
        Driver, maria.id).branch_id

    assert AssigneeScopeService().covers_vehicle(juan_user, v1.id) is False


def test_covers_vehicle_false_for_an_unlinked_user(app, env):
    _ju, _mu, juan, _m, v1, _v2 = env
    VehicleAssignmentService().assign(v1.id, juan.id, source="MANUAL")
    stranger = User(username="stranger", email="s@example.com",
                    password_hash=hash_password("x"), is_active=True)
    db.session.add(stranger)
    db.session.commit()

    assert AssigneeScopeService().covers_vehicle(stranger, v1.id) is False


def test_covers_vehicle_false_for_none_user(app, env):
    _ju, _mu, juan, _m, v1, _v2 = env
    VehicleAssignmentService().assign(v1.id, juan.id, source="MANUAL")
    assert AssigneeScopeService().covers_vehicle(None, v1.id) is False
