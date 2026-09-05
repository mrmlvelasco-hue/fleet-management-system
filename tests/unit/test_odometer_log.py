"""Odometer readings are recorded, not just applied.

The client asked how to check an update pushed from the phone. Nothing
recorded it: every path overwrote Vehicle.current_odometer and left no
trace of who set it, when, or what it was before.

That matters beyond curiosity -- current_odometer drives PM due status,
so a wrong reading silently changes when a vehicle is called in for
service.
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
from app.modules.master_data.vehicle.odometer_models import VehicleOdometerLog
from app.modules.master_data.vehicle.odometer_service import (
    OdometerError, OdometerService)
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.user_management.models import Permission, Role, User

CODES = ["vehicle.view", "vehicle.update"]


def _perm(code):
    p = Permission.query.filter_by(code=code).first()
    if p is None:
        m, a = code.split(".")
        p = Permission(code=code, module=m, action=a)
        db.session.add(p)
        db.session.flush()
    return p


def _user(username, branch_id):
    role = Role(name=f"r-{username}", description="r")
    db.session.add(role)
    db.session.flush()
    for c in CODES:
        role.permissions.append(_perm(c))
    u = User(username=username, email=f"{username}@e.com",
             password_hash=hash_password("secret123"), is_active=True,
             branch_id=branch_id, mobile_access=True, first_name="Juan",
             last_name="Cruz")
    u.roles.append(role)
    db.session.add(u)
    db.session.commit()
    return u


@pytest.fixture()
def env(app):
    b = Branch(code="CAR", name="Carmona")
    vt = VehicleType(code="LT", name="Light", category="LIGHT")
    db.session.add_all([b, vt])
    db.session.commit()
    ju = _user("juan", b.id)
    juan = Driver(person_id="P1", employee_number="E1", first_name="Juan",
                  last_name="Cruz", assignee_type="DRIVER", branch_id=b.id)
    db.session.add(juan)
    db.session.commit()
    DriverService().link_user(juan.id, ju.id)
    v = VehicleService().create(vehicle_type_id=vt.id, brand="Toyota",
                                model="Vios", year=2009, branch_id=b.id,
                                conduction_number="A1", plate_number="NZO-619")
    v.current_odometer = 7000
    db.session.commit()
    VehicleAssignmentService().assign(v.id, juan.id, source="MANUAL")
    return v, ju


def _hdr(client, username="juan"):
    r = client.post("/api/v1/auth/token",
                    json={"username": username, "password": "secret123"})
    return {"Authorization": f"Bearer "
            f"{json.loads(r.get_data(as_text=True))['access_token']}"}


def _body(r):
    return json.loads(r.get_data(as_text=True))


def test_recording_moves_the_vehicle_and_writes_a_log(app, env):
    v, ju = env
    OdometerService().record(v.id, 7150, source="MOBILE", user=ju)

    assert db.session.get(Vehicle, v.id).current_odometer == 7150
    entry = VehicleOdometerLog.query.filter_by(vehicle_id=v.id).one()
    assert entry.reading == 7150
    assert entry.previous_reading == 7000
    assert entry.delta == 150
    assert entry.source == "MOBILE"
    assert entry.recorded_by == ju.id


def test_a_rollback_is_refused_by_default(app, env):
    """current_odometer drives PM due status, so a fat-fingered digit
    must not silently move a vehicle's service schedule backwards."""
    v, ju = env
    with pytest.raises(OdometerError):
        OdometerService().record(v.id, 100, source="MOBILE", user=ju)


def test_a_correction_is_possible_deliberately(app, env):
    """A mis-keyed value is legitimate to fix -- but as an explicit act,
    not something a daily entry does by accident."""
    v, ju = env
    OdometerService().record(v.id, 100, source="WEB", user=ju,
                             allow_rollback=True, remarks="Mis-keyed")
    assert db.session.get(Vehicle, v.id).current_odometer == 100


def test_the_first_reading_measures_from_the_enrolled_value(app):
    """Vehicle.current_odometer defaults to 0, so the first recorded
    reading measures from there and delta equals the reading.

    Deliberately NOT special-cased. Treating a previous of 0 as "no
    reading" would misreport a genuinely new vehicle, which really does
    start at zero. The enrolment case -- a used vehicle added at
    120,000 km showing a 120,000 km first delta -- is better handled by
    entering its odometer on the vehicle record at enrolment, which is
    already a field on the form.
    """
    b = Branch(code="X", name="X")
    vt = VehicleType(code="T", name="T", category="LIGHT")
    db.session.add_all([b, vt])
    db.session.commit()
    v = VehicleService().create(vehicle_type_id=vt.id, brand="B", model="M",
                                year=2020, branch_id=b.id,
                                conduction_number="C", plate_number="P")
    db.session.commit()

    entry = OdometerService().record(v.id, 120000, source="WEB")

    assert entry.previous_reading == 0
    assert entry.delta == 120000


# ── the mobile push, and checking it ─────────────────────────────────

def test_a_mobile_push_is_recorded_as_MOBILE(app, client, env):
    """The client's actual question. Without the source recorded at the
    moment of writing, "which readings came from the phone" cannot be
    answered at all."""
    v, _ju = env

    r = client.post(f"/api/v1/my/vehicles/{v.id}/odometer",
                    json={"odometer": 7150}, headers=_hdr(client))

    assert r.status_code == 200
    entry = VehicleOdometerLog.query.filter_by(vehicle_id=v.id).one()
    assert entry.source == "MOBILE"


def test_the_driver_can_see_their_own_history(app, client, env):
    v, _ju = env
    client.post(f"/api/v1/my/vehicles/{v.id}/odometer",
                json={"odometer": 7150}, headers=_hdr(client))

    body = _body(client.get(f"/api/v1/my/vehicles/{v.id}/odometer",
                            headers=_hdr(client)))

    assert body["total"] == 1
    assert body["items"][0]["reading"] == 7150
    assert body["items"][0]["delta"] == 150


def test_the_history_is_assignment_scoped(app, client, env):
    """Same rule as everything else in /my/*: a vehicle you do not hold
    is a 404, not a 403."""
    v, _ju = env
    b = Branch.query.first()
    _user("stranger", b.id)

    r = client.get(f"/api/v1/my/vehicles/{v.id}/odometer",
                   headers=_hdr(client, "stranger"))

    assert r.status_code == 404


# ── the admin list ───────────────────────────────────────────────────

def test_the_admin_list_shows_readings(app, client, env):
    v, ju = env
    OdometerService().record(v.id, 7150, source="MOBILE", user=ju)

    body = _body(client.get("/api/v1/vehicles/odometer-logs",
                            headers=_hdr(client)))

    assert body["total"] == 1
    row = body["items"][0]
    assert row["plate_number"] == "NZO-619"
    assert row["source"] == "MOBILE"
    assert row["recorded_by"] == "Juan Cruz"


def test_the_admin_list_filters_by_source(app, client, env):
    """"Which readings came from mobile last week" is the question
    actually being asked."""
    v, ju = env
    OdometerService().record(v.id, 7100, source="WEB", user=ju)
    OdometerService().record(v.id, 7150, source="MOBILE", user=ju)

    body = _body(client.get("/api/v1/vehicles/odometer-logs?source=MOBILE",
                            headers=_hdr(client)))

    assert body["total"] == 1
    assert body["items"][0]["source"] == "MOBILE"


def test_the_admin_list_is_newest_first(app, client, env):
    v, ju = env
    OdometerService().record(v.id, 7100, source="WEB", user=ju)
    OdometerService().record(v.id, 7200, source="MOBILE", user=ju)

    body = _body(client.get("/api/v1/vehicles/odometer-logs",
                            headers=_hdr(client)))

    assert [r["reading"] for r in body["items"]] == [7200, 7100]
