"""The /api/v1/my/* namespace -- assignment scope, not org scope.

This is where AssigneeScopeService starts guarding something.

Every test here puts a SECOND vehicle in the SAME branch, held by
somebody else, and asserts the caller cannot reach it. That shape is
deliberate: org scope would say yes to all of them, and org scope
saying yes is exactly finding J1. A test that only checked a vehicle in
a different branch would pass against the broken behaviour.

404, not 403, throughout. Inside /my/*, a vehicle you are not assigned
is not merely forbidden -- it is not part of your world, and a 403 would
confirm the record exists, turning sequential ids and plate numbers into
an enumeration oracle.
"""
import json

import pytest

from app.core.security.password import hash_password
from app.extensions import db
from app.modules.master_data.driver.models import Driver
from app.modules.master_data.driver.service import DriverService
from app.modules.master_data.org.models import Branch
from app.modules.master_data.reference.models import VehicleType
from app.modules.master_data.vehicle.assignment_service import (
    VehicleAssignmentService)
from app.modules.master_data.vehicle.models import Vehicle
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.user_management.models import Permission, Role, User


def _perm(code):
    p = Permission.query.filter_by(code=code).first()
    if p is None:
        module, action = code.split(".")
        p = Permission(code=code, module=module, action=action)
        db.session.add(p)
        db.session.flush()
    return p


def _user(username, codes, branch_id):
    role = Role(name=f"r-{username}", description="r")
    db.session.add(role)
    db.session.flush()
    for c in codes:
        role.permissions.append(_perm(c))
    u = User(username=username, email=f"{username}@example.com",
             password_hash=hash_password("secret123"), is_active=True,
             branch_id=branch_id, mobile_access=True)
    u.roles.append(role)
    db.session.add(u)
    db.session.commit()
    return u


FIELD_CODES = ["vehicle.view", "vehicle.update"]


@pytest.fixture()
def env(app):
    b = Branch(code="CAR", name="Carmona")
    vt = VehicleType(code="LT", name="Light", category="LIGHT")
    db.session.add_all([b, vt])
    db.session.commit()

    juan_u = _user("juan", FIELD_CODES, b.id)
    maria_u = _user("maria", FIELD_CODES, b.id)

    juan = Driver(person_id="PID-1", employee_number="EMP-001",
                  first_name="Juan", last_name="Cruz",
                  assignee_type="DRIVER", branch_id=b.id)
    maria = Driver(person_id="PID-2", employee_number="EMP-002",
                   first_name="Maria", last_name="Santos",
                   assignee_type="DRIVER", branch_id=b.id)
    db.session.add_all([juan, maria])
    db.session.commit()
    DriverService().link_user(juan.id, juan_u.id)
    DriverService().link_user(maria.id, maria_u.id)

    mine = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2024,
        branch_id=b.id, conduction_number="A-001", plate_number="MINE-111")
    theirs = VehicleService().create(
        vehicle_type_id=vt.id, brand="Isuzu", model="DMax", year=2023,
        branch_id=b.id, conduction_number="A-002", plate_number="HERS-222")
    db.session.commit()

    svc = VehicleAssignmentService()
    svc.assign(mine.id, juan.id, source="MANUAL")
    svc.assign(theirs.id, maria.id, source="MANUAL")
    return b, vt, juan_u, maria_u, juan, maria, mine, theirs


def _hdr(client, username="juan"):
    r = client.post("/api/v1/auth/token",
                    json={"username": username, "password": "secret123"})
    tok = json.loads(r.get_data(as_text=True))["access_token"]
    return {"Authorization": f"Bearer {tok}"}


def _body(r):
    return json.loads(r.get_data(as_text=True))


# ── list ─────────────────────────────────────────────────────────────

def test_list_returns_only_my_vehicles(app, client, env):
    _b, _vt, _ju, _mu, _j, _m, mine, theirs = env

    r = client.get("/api/v1/my/vehicles", headers=_hdr(client))

    assert r.status_code == 200
    ids = [v["id"] for v in _body(r)["items"]]
    assert ids == [mine.id]
    # Same branch. Org scope would have included it.
    assert theirs.id not in ids


def test_list_is_empty_for_an_unlinked_user(app, client, env):
    """Empty, not an error. Most users are not assignees, and the app
    must render a clean 'nothing assigned to you' rather than a
    failure."""
    b, _vt, _ju, _mu, _j, _m, _mine, _theirs = env
    _user("stranger", FIELD_CODES, b.id)

    r = client.get("/api/v1/my/vehicles", headers=_hdr(client, "stranger"))

    assert r.status_code == 200
    assert _body(r)["items"] == []


def test_a_released_vehicle_disappears_from_the_list(app, client, env):
    _b, _vt, _ju, _mu, _j, _m, mine, _theirs = env
    VehicleAssignmentService().release(mine.id, source="MANUAL")

    r = client.get("/api/v1/my/vehicles", headers=_hdr(client))

    assert _body(r)["items"] == []


# ── detail ───────────────────────────────────────────────────────────

def test_detail_returns_my_vehicle(app, client, env):
    _b, _vt, _ju, _mu, _j, _m, mine, _theirs = env
    r = client.get(f"/api/v1/my/vehicles/{mine.id}", headers=_hdr(client))
    assert r.status_code == 200
    assert _body(r)["plate_number"] == "MINE-111"


def test_detail_404s_for_a_branchmates_vehicle(app, client, env):
    """THE J1 test. Juan and Maria share a branch, so org scope covers
    this vehicle for Juan -- and that is correct for a Fleet Officer,
    wrong for a driver."""
    _b, _vt, _ju, _mu, _j, _m, _mine, theirs = env

    r = client.get(f"/api/v1/my/vehicles/{theirs.id}", headers=_hdr(client))

    assert r.status_code == 404


def test_a_hidden_vehicle_looks_the_same_as_a_missing_one(app, client, env):
    _b, _vt, _ju, _mu, _j, _m, _mine, theirs = env
    hidden = client.get(f"/api/v1/my/vehicles/{theirs.id}",
                        headers=_hdr(client))
    absent = client.get("/api/v1/my/vehicles/999999", headers=_hdr(client))
    assert hidden.status_code == absent.status_code == 404


def test_detail_404s_after_the_vehicle_is_released(app, client, env):
    """Handing a vehicle back must end access to it. A closed
    assignment is not coverage."""
    _b, _vt, _ju, _mu, _j, _m, mine, _theirs = env
    VehicleAssignmentService().release(mine.id, source="MANUAL")

    r = client.get(f"/api/v1/my/vehicles/{mine.id}", headers=_hdr(client))

    assert r.status_code == 404


# ── odometer (J2) ────────────────────────────────────────────────────

def test_odometer_accepted_for_my_vehicle(app, client, env):
    _b, _vt, _ju, _mu, _j, _m, mine, _theirs = env

    r = client.post(f"/api/v1/my/vehicles/{mine.id}/odometer",
                    json={"odometer": 12345}, headers=_hdr(client))

    assert r.status_code == 200
    assert db.session.get(Vehicle, mine.id).current_odometer == 12345


def test_odometer_404s_for_a_branchmates_vehicle_and_writes_nothing(
        app, client, env):
    """J2. The refusal is not enough on its own -- the reading must not
    land."""
    _b, _vt, _ju, _mu, _j, _m, _mine, theirs = env
    before = db.session.get(Vehicle, theirs.id).current_odometer

    r = client.post(f"/api/v1/my/vehicles/{theirs.id}/odometer",
                    json={"odometer": 99999}, headers=_hdr(client))

    assert r.status_code == 404
    assert db.session.get(Vehicle, theirs.id).current_odometer == before
