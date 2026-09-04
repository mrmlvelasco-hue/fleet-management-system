"""Splitting checklist.submit into submit + review.

The client asked for drivers to submit their own inspections, with
SUBMITTED meaning "final, ready for Fleet to review".

That could not be granted by simply giving drivers checklist.submit,
because that one code was doing TWO unrelated jobs: "may finalise" and
"is a reviewer". It gated five things -- list scope, detail scope,
create scope, delete scope, and the submit action itself. Granting it to
drivers would have silently handed every driver the whole fleet's
inspections and REOPENED the create leak fixed two rounds ago, with
nothing erroring.

So:

    checklist.submit  -- finalise MY OWN checklist   (driver + fleet)
    checklist.review  -- see and act on EVERYONE'S   (fleet only)

Every scoping guard moves to `review`. Only the submit action keeps
`submit`. These tests pin that separation, because the failure mode is
silent widening rather than an error.
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
from app.modules.transactions.vehicle_checklist.service import (
    VehicleChecklistService)
from app.modules.user_management.models import Permission, Role, User

#: A driver AFTER the split: may finalise their own, may not review.
DRIVER = ["vehicle.view", "checklist.view", "checklist.create",
          "checklist.update", "checklist.submit"]
#: A Fleet Officer: both.
FLEET = DRIVER + ["checklist.review"]


def _perm(code):
    p = Permission.query.filter_by(code=code).first()
    if p is None:
        m, a = code.split(".")
        p = Permission(code=code, module=m, action=a)
        db.session.add(p)
        db.session.flush()
    return p


def _user(username, codes, branch_id):
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
    b = Branch(code="CAR", name="Carmona")
    vt = VehicleType(code="LT", name="Light", category="LIGHT")
    db.session.add_all([b, vt])
    db.session.commit()
    ju = _user("juan", DRIVER, b.id)
    mu = _user("maria", DRIVER, b.id)
    fu = _user("fleet", FLEET, b.id)
    juan = Driver(person_id="P1", employee_number="E1", first_name="Juan",
                  last_name="Cruz", assignee_type="DRIVER", branch_id=b.id)
    maria = Driver(person_id="P2", employee_number="E2", first_name="Maria",
                   last_name="Santos", assignee_type="DRIVER", branch_id=b.id)
    db.session.add_all([juan, maria])
    db.session.commit()
    DriverService().link_user(juan.id, ju.id)
    DriverService().link_user(maria.id, mu.id)
    mine = VehicleService().create(vehicle_type_id=vt.id, brand="Toyota",
                                   model="Vios", year=2009, branch_id=b.id,
                                   conduction_number="A1",
                                   plate_number="MINE-1")
    theirs = VehicleService().create(vehicle_type_id=vt.id, brand="Toyota",
                                     model="Vios", year=2009, branch_id=b.id,
                                     conduction_number="A2",
                                     plate_number="HERS-2")
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


def _create(client, vid, tid, username="juan"):
    return client.post("/api/v1/checklists", headers=_hdr(client, username),
                       json={"vehicle_id": vid, "template_id": tid})


def _body(r):
    return json.loads(r.get_data(as_text=True))


# ── the driver gains submit, and NOTHING else ────────────────────────

def test_a_driver_holding_submit_still_sees_only_their_own(app, client, env):
    """The whole point of the split. Before it, granting submit so a
    driver could finalise their own inspection would also have shown
    them everyone else's."""
    mine, theirs, tpl = env
    _create(client, mine.id, tpl.id, "juan")
    # Maria raises one on her own vehicle.
    _create(client, theirs.id, tpl.id, "maria")

    rows = _body(client.get("/api/v1/checklists",
                            headers=_hdr(client, "juan")))["items"]

    assert len(rows) == 1
    assert rows[0]["vehicle_id"] == mine.id


def test_a_driver_holding_submit_still_cannot_open_anothers(app, client, env):
    _mine, theirs, tpl = env
    cid = _body(_create(client, theirs.id, tpl.id, "maria"))["id"]

    r = client.get(f"/api/v1/checklists/{cid}", headers=_hdr(client, "juan"))

    assert r.status_code == 404


def test_a_driver_holding_submit_still_cannot_use_anothers_vehicle(
        app, client, env):
    """The leak that would have reopened. checklist.submit used to widen
    create scope to the whole branch."""
    _mine, theirs, tpl = env

    r = _create(client, theirs.id, tpl.id, "juan")

    assert r.status_code == 403


def test_a_driver_holding_submit_cannot_delete_anothers_draft(app, client,
                                                               env):
    _mine, theirs, tpl = env
    cid = _body(_create(client, theirs.id, tpl.id, "maria"))["id"]

    r = client.delete(f"/api/v1/checklists/{cid}", headers=_hdr(client, "juan"))

    assert r.status_code == 404


# ── the reviewer keeps everything ────────────────────────────────────

def test_a_reviewer_sees_every_checklist(app, client, env):
    mine, theirs, tpl = env
    _create(client, mine.id, tpl.id, "juan")
    _create(client, theirs.id, tpl.id, "maria")

    rows = _body(client.get("/api/v1/checklists",
                            headers=_hdr(client, "fleet")))["items"]

    assert len(rows) == 2


def test_a_reviewer_may_create_against_any_vehicle_in_scope(app, client, env):
    _mine, theirs, tpl = env
    assert _create(client, theirs.id, tpl.id, "fleet").status_code == 201


def test_a_reviewer_may_open_anothers_checklist(app, client, env):
    mine, _theirs, tpl = env
    cid = _body(_create(client, mine.id, tpl.id, "juan"))["id"]

    r = client.get(f"/api/v1/checklists/{cid}", headers=_hdr(client, "fleet"))

    assert r.status_code == 200


# ── a user with NEITHER code ─────────────────────────────────────────

def test_review_alone_does_not_grant_submit(app, client, env):
    """The two codes are independent. A reviewer-only account cannot
    finalise; a submitter-only account cannot review."""
    b = Branch.query.first()
    _user("auditor", ["vehicle.view", "checklist.view", "checklist.review"],
          b.id)
    mine, _theirs, tpl = env
    cid = _body(_create(client, mine.id, tpl.id, "juan"))["id"]

    r = client.post(f"/api/v1/checklists/{cid}/submit",
                    headers=_hdr(client, "auditor"), json={})

    assert r.status_code == 403
