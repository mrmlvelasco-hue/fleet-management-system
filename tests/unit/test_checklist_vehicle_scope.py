"""A driver may only raise a checklist against a vehicle assigned to them.

Found on a real device: the New Vehicle Checklist picker listed the
whole branch -- NAO-907, NAO-917, NCI-742, NFO-839 and so on -- for a
driver holding one vehicle. That is finding J1 surfacing in a screen
built before the /my/* namespace existed: the picker calls
/reference/vehicles, which scopes on ORG, and nothing on create checked
the vehicle at all.

Narrowing the picker alone would not fix it. A picker is a convenience;
the create endpoint is the control. So the check lives here, and the
picker follows.

`checklist.review` is the reviewer signal, exactly as it is for list and
detail visibility. A Fleet Officer legitimately raises checklists
against any vehicle in scope; a driver does not.
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
from app.modules.transactions.vehicle_checklist.models import VehicleChecklist
from app.modules.transactions.vehicle_checklist.service import (
    VehicleChecklistService)
from app.modules.user_management.models import Permission, Role, User

DRIVER_CODES = ["vehicle.view", "checklist.view", "checklist.create",
                "checklist.update"]


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


@pytest.fixture()
def env(app):
    b = Branch(code="CAR", name="Carmona")
    vt = VehicleType(code="LT", name="Light", category="LIGHT")
    db.session.add_all([b, vt])
    db.session.commit()

    ju = _user("juan", DRIVER_CODES, b.id)
    fu = _user("fleet", DRIVER_CODES + ["checklist.review",
                                        "checklist.submit"], b.id)

    juan = Driver(person_id="PID-1", employee_number="EMP-001",
                  first_name="Juan", last_name="Cruz",
                  assignee_type="DRIVER", branch_id=b.id)
    maria = Driver(person_id="PID-2", employee_number="EMP-002",
                   first_name="Maria", last_name="Santos",
                   assignee_type="DRIVER", branch_id=b.id)
    db.session.add_all([juan, maria])
    db.session.commit()
    DriverService().link_user(juan.id, ju.id)

    mine = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2009,
        branch_id=b.id, conduction_number="A-001", plate_number="NAO-907")
    theirs = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2009,
        branch_id=b.id, conduction_number="A-002", plate_number="NAO-917")
    db.session.commit()
    svc = VehicleAssignmentService()
    svc.assign(mine.id, juan.id, source="MANUAL")
    svc.assign(theirs.id, maria.id, source="MANUAL")

    tpl = VehicleChecklistService().create_template(name="Daily")
    db.session.commit()
    return mine, theirs, tpl


def _hdr(client, username="juan"):
    r = client.post("/api/v1/auth/token",
                    json={"username": username, "password": "secret123"})
    return {"Authorization": f"Bearer "
            f"{json.loads(r.get_data(as_text=True))['access_token']}"}


def _create(client, vehicle_id, tpl_id, username="juan"):
    return client.post("/api/v1/checklists", headers=_hdr(client, username),
                       json={"vehicle_id": vehicle_id,
                             "template_id": tpl_id})


def test_a_driver_may_check_their_own_vehicle(app, client, env):
    mine, _theirs, tpl = env
    assert _create(client, mine.id, tpl.id).status_code == 201


def test_a_driver_may_NOT_check_a_branchmates_vehicle(app, client, env):
    """The leak. Same branch, so org scope said yes and the picker
    offered it."""
    _mine, theirs, tpl = env

    r = _create(client, theirs.id, tpl.id)

    assert r.status_code == 403
    assert VehicleChecklist.query.filter_by(
        vehicle_id=theirs.id).count() == 0


def test_the_refusal_explains_itself(app, client, env):
    _mine, theirs, tpl = env
    body = json.loads(_create(client, theirs.id, tpl.id).get_data(
        as_text=True))
    assert "assigned" in body["message"].lower()


def test_a_fleet_reviewer_may_check_any_vehicle_in_scope(app, client, env):
    """checklist.review is the reviewer signal, as it is for list and
    detail. A Fleet Officer legitimately raises checklists against any
    vehicle they can see."""
    _mine, theirs, tpl = env
    assert _create(client, theirs.id, tpl.id, "fleet").status_code == 201


def test_an_unlinked_user_without_submit_cannot_create_at_all(app, client,
                                                               env):
    """No assignee record means no assigned vehicles, so there is no
    vehicle they may raise a checklist against. Refusing is correct;
    silently allowing everything would be the bug."""
    b = Branch.query.first()
    _user("stranger", DRIVER_CODES, b.id)
    mine, _theirs, tpl = env

    assert _create(client, mine.id, tpl.id, "stranger").status_code == 403


def test_a_released_vehicle_is_no_longer_checkable(app, client, env):
    mine, _theirs, tpl = env
    VehicleAssignmentService().release(mine.id, source="MANUAL")

    assert _create(client, mine.id, tpl.id).status_code == 403


# ── the picker itself ────────────────────────────────────────────────

def test_scope_assigned_offers_only_my_vehicle(app, client, env):
    """The picker convenience. Not the control -- a hand-built request
    ignores this -- but it stops the app offering a vehicle the server
    would refuse, which is what the driver actually saw."""
    mine, theirs, _tpl = env

    body = json.loads(client.get(
        "/api/v1/reference/vehicles?scope=assigned",
        headers=_hdr(client)).get_data(as_text=True))

    ids = [v["id"] for v in body["items"]]
    assert ids == [mine.id]
    assert theirs.id not in ids


def test_without_the_scope_the_picker_is_unchanged(app, client, env):
    """Trip tickets and maintenance orders on the web still need org
    scope, so the default must not move."""
    mine, theirs, _tpl = env

    body = json.loads(client.get(
        "/api/v1/reference/vehicles", headers=_hdr(client)
    ).get_data(as_text=True))

    ids = [v["id"] for v in body["items"]]
    assert mine.id in ids and theirs.id in ids


def test_an_unlinked_user_gets_an_empty_assigned_picker(app, client, env):
    b = Branch.query.first()
    _user("stranger", DRIVER_CODES, b.id)

    body = json.loads(client.get(
        "/api/v1/reference/vehicles?scope=assigned",
        headers=_hdr(client, "stranger")).get_data(as_text=True))

    assert body["items"] == []
