"""Odometer history is scoped, per the client's requirement:

    Vehicle Assignee  -> only their assigned vehicles
    Fleet (branch-scoped) role -> only vehicles in their assigned branches
    No branch assignment -> global access

Found broken: /api/v1/vehicles/odometer-logs carried only
@api_auth_required("vehicle.view") and no scoping at all. Anyone holding
that one permission saw every vehicle's odometer history company-wide,
identical to the vehicle-list gap fixed earlier for the same reason --
this endpoint was simply never audited alongside it.

The fix reuses the two services that already implement these rules
correctly (UserOrgScopeService for branch scope, AssigneeScopeService
for assignee scope) rather than inventing a third copy of the logic.
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
from app.modules.master_data.vehicle.odometer_service import OdometerService
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.user_management.models import Permission, Role, User
from app.modules.user_management.org_scope_service import UserOrgScopeService


def _perm(code):
    p = Permission.query.filter_by(code=code).first()
    if p is None:
        m, a = code.split(".")
        p = Permission(code=code, module=m, action=a)
        db.session.add(p)
        db.session.flush()
    return p


def _user(username, codes, branch_id=None):
    role = Role(name=f"r-{username}", description="r")
    db.session.add(role)
    db.session.flush()
    for c in codes:
        role.permissions.append(_perm(c))
    u = User(username=username, email=f"{username}@e.com",
             password_hash=hash_password("secret123"), is_active=True,
             branch_id=branch_id, mobile_access=True)
    u.roles.append(role)
    db.session.add(u)
    db.session.commit()
    return u


@pytest.fixture()
def env(app):
    east = Branch(code="EAL", name="East Asia Laboratories Inc.")
    west = Branch(code="MNL", name="Manila")
    vt = VehicleType(code="LT", name="Light", category="LIGHT")
    db.session.add_all([east, west, vt])
    db.session.commit()

    v_east = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2009,
        branch_id=east.id, conduction_number="E1", plate_number="EAL-001")
    v_west = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2009,
        branch_id=west.id, conduction_number="W1", plate_number="MNL-001")
    db.session.commit()

    OdometerService().record(v_east.id, 5000, source="MOBILE")
    OdometerService().record(v_west.id, 8000, source="MOBILE")

    return east, west, v_east, v_west


def _hdr(client, username):
    r = client.post("/api/v1/auth/token",
                    json={"username": username, "password": "secret123"})
    return {"Authorization": f"Bearer "
            f"{json.loads(r.get_data(as_text=True))['access_token']}"}


def _body(r):
    return json.loads(r.get_data(as_text=True))


# ── branch scoping ───────────────────────────────────────────────────

def test_a_branch_scoped_user_sees_only_their_branchs_readings(app, client,
                                                               env):
    """The client's reported symptom, on the odometer screen specifically:
    LBautista, assigned only to East Asia Laboratories Inc., must not see
    Manila's readings."""
    east, _west, v_east, v_west = env
    u = _user("lbautista", ["vehicle.view"])
    UserOrgScopeService().assign(u.id, scope_type="BRANCH",
                                 branch_id=east.id)

    rows = _body(client.get("/api/v1/vehicles/odometer-logs",
                            headers=_hdr(client, "lbautista")))["items"]

    vehicle_ids = {r["vehicle_id"] for r in rows}
    assert vehicle_ids == {v_east.id}
    assert v_west.id not in vehicle_ids


def test_a_user_with_no_branch_assignment_sees_everything(app, client, env):
    """Global access, per the client's expected architecture: no
    UserOrgScope rows at all means unrestricted."""
    _east, _west, v_east, v_west = env
    _user("admin", ["vehicle.view"])

    rows = _body(client.get("/api/v1/vehicles/odometer-logs",
                            headers=_hdr(client, "admin")))["items"]

    vehicle_ids = {r["vehicle_id"] for r in rows}
    assert v_east.id in vehicle_ids
    assert v_west.id in vehicle_ids


def test_a_GLOBAL_scope_also_sees_everything(app, client, env):
    """An approver explicitly granted GLOBAL -- not merely lacking a
    scope -- must see every branch too."""
    _east, _west, v_east, v_west = env
    u = _user("approver", ["vehicle.view"])
    UserOrgScopeService().assign(u.id, scope_type="GLOBAL")

    rows = _body(client.get("/api/v1/vehicles/odometer-logs",
                            headers=_hdr(client, "approver")))["items"]

    vehicle_ids = {r["vehicle_id"] for r in rows}
    assert v_east.id in vehicle_ids and v_west.id in vehicle_ids


# ── assignee scoping ─────────────────────────────────────────────────

def test_an_assignee_sees_only_their_assigned_vehicle(app, client, env):
    """Vehicle Assignee restricted to assigned vehicles, per the
    client's expected architecture -- narrower than branch scope, since
    a driver's branch-mate's vehicle is not theirs either."""
    east, _west, v_east, v_west = env
    u = _user("juan", ["vehicle.view"], branch_id=east.id)
    driver = Driver(person_id="P1", employee_number="E1",
                    first_name="Juan", last_name="Cruz",
                    assignee_type="DRIVER", branch_id=east.id)
    db.session.add(driver)
    db.session.commit()
    DriverService().link_user(driver.id, u.id)
    VehicleAssignmentService().assign(v_east.id, driver.id, source="MANUAL")

    rows = _body(client.get("/api/v1/vehicles/odometer-logs",
                            headers=_hdr(client, "juan")))["items"]

    vehicle_ids = {r["vehicle_id"] for r in rows}
    assert vehicle_ids == {v_east.id}
    assert v_west.id not in vehicle_ids


def test_an_assignee_with_no_vehicle_sees_nothing(app, client, env):
    """Not global access. Being a DRIVER with no assignment is not the
    same as having no branch assignment -- the assignee rule is stricter
    and must not fall back to org scope."""
    east, _west, _v_east, _v_west = env
    u = _user("maria", ["vehicle.view"], branch_id=east.id)
    driver = Driver(person_id="P2", employee_number="E2",
                    first_name="Maria", last_name="Santos",
                    assignee_type="DRIVER", branch_id=east.id)
    db.session.add(driver)
    db.session.commit()
    DriverService().link_user(driver.id, u.id)

    rows = _body(client.get("/api/v1/vehicles/odometer-logs",
                            headers=_hdr(client, "maria")))["items"]

    assert rows == []


# ── plate search ─────────────────────────────────────────────────────

def test_plate_search_narrows_the_list(app, client, env):
    _east, _west, v_east, v_west = env
    _user("admin", ["vehicle.view"])

    rows = _body(client.get(
        "/api/v1/vehicles/odometer-logs?plate=EAL",
        headers=_hdr(client, "admin")))["items"]

    vehicle_ids = {r["vehicle_id"] for r in rows}
    assert vehicle_ids == {v_east.id}


def test_plate_search_is_case_insensitive_and_matches_conduction_no(app,
                                                                    client,
                                                                    env):
    _east, _west, v_east, _v_west = env
    _user("admin", ["vehicle.view"])

    rows = _body(client.get(
        "/api/v1/vehicles/odometer-logs?plate=e1",
        headers=_hdr(client, "admin")))["items"]

    assert {r["vehicle_id"] for r in rows} == {v_east.id}


def test_plate_search_combines_with_branch_scope(app, client, env):
    """A branch-scoped user searching a plate outside their branch gets
    nothing, not the other branch's vehicle."""
    east, _west, v_east, v_west = env
    u = _user("lbautista", ["vehicle.view"])
    UserOrgScopeService().assign(u.id, scope_type="BRANCH",
                                 branch_id=east.id)

    rows = _body(client.get(
        "/api/v1/vehicles/odometer-logs?plate=MNL",
        headers=_hdr(client, "lbautista")))["items"]

    assert rows == []
