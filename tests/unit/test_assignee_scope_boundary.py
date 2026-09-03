"""The boundary Phase 2 draws, pinned so nobody has to infer it.

Phase 2 closes J1/J2/J4 for the mobile audience by giving the field app
a namespace that never touches the org-scoped endpoints. It does NOT
narrow /api/v1/vehicles/*, because the web app, the telematics feed and
external integrations legitimately operate on org scope.

Two consequences, and both are easy to get wrong later:

1. /my/* is safe EVEN FOR a caller holding vehicle.view. The scope does
   not depend on withholding permissions -- if it did, the whole design
   would rest on seed data nobody guards.

2. /api/v1/vehicles/* REMAINS branch-wide for such a caller. That is
   deliberate and correct for its audience, and it is exactly why the
   mobile client must call /my/* and never the general endpoints.

The second test below therefore asserts behaviour that looks like a
vulnerability in isolation. It is pinned on purpose: if someone later
"fixes" it, they will break the web app and the telematics feed, and
this test is where they will find out why it is that way.
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


@pytest.fixture()
def env(app):
    b = Branch(code="CAR", name="Carmona")
    vt = VehicleType(code="LT", name="Light", category="LIGHT")
    db.session.add_all([b, vt])
    db.session.commit()

    role = Role(name="Vehicle Assignee", description="field")
    db.session.add(role)
    db.session.flush()
    # Deliberately GRANTED vehicle.view -- the point of the first test.
    for c in ("vehicle.view", "vehicle.update"):
        role.permissions.append(_perm(c))
    u = User(username="juan", email="juan@example.com",
             password_hash=hash_password("secret123"), is_active=True,
             branch_id=b.id, mobile_access=True)
    u.roles.append(role)
    db.session.add(u)
    db.session.commit()

    juan = Driver(person_id="PID-1", employee_number="EMP-001",
                  first_name="Juan", last_name="Cruz",
                  assignee_type="DRIVER", branch_id=b.id)
    maria = Driver(person_id="PID-2", employee_number="EMP-002",
                   first_name="Maria", last_name="Santos",
                   assignee_type="DRIVER", branch_id=b.id)
    db.session.add_all([juan, maria])
    db.session.commit()
    DriverService().link_user(juan.id, u.id)

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
    return mine, theirs


def _hdr(client):
    r = client.post("/api/v1/auth/token",
                    json={"username": "juan", "password": "secret123"})
    return {"Authorization": f"Bearer "
            f"{json.loads(r.get_data(as_text=True))['access_token']}"}


def test_my_scope_holds_even_for_a_caller_with_vehicle_view(app, client,
                                                             env):
    """The load-bearing one.

    If /my/* only worked because assignees were denied vehicle.view,
    the entire scope model would rest on seed data that nobody guards,
    and the next administrator to hand a driver a broader role would
    silently reopen J1. It does not: the namespace scopes on
    ASSIGNMENT, independently of what the caller may do.
    """
    _mine, theirs = env

    r = client.get(f"/api/v1/my/vehicles/{theirs.id}", headers=_hdr(client))

    assert r.status_code == 404


def test_the_general_endpoint_is_still_org_scoped(app, client, env):
    """Pinned, not endorsed.

    This asserts that /api/v1/vehicles/<id> still returns a branch-mate's
    vehicle to a caller with vehicle.view. That is correct for its
    audience -- the web app and the telematics feed need it -- and it is
    precisely why the mobile client must use /my/* instead.

    If a future change makes this 404, the web Vehicle Master screen and
    the telematics odometer feed break. This test is where that will be
    explained, rather than discovered in production.
    """
    _mine, theirs = env

    r = client.get(f"/api/v1/vehicles/{theirs.id}", headers=_hdr(client))

    assert r.status_code == 200


def test_my_vehicles_never_returns_more_than_the_assignments(app, client,
                                                              env):
    mine, theirs = env
    r = client.get("/api/v1/my/vehicles", headers=_hdr(client))
    ids = [v["id"] for v in json.loads(r.get_data(as_text=True))["items"]]
    assert ids == [mine.id]
    assert theirs.id not in ids
