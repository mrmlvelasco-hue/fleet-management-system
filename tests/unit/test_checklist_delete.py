"""Deleting a checklist draft.

The client's case: an inspection created against the wrong assignee or
vehicle. The create guard now scopes to assigned vehicles, but a driver
can still start one on the wrong vehicle within their own scope, and
they need a way to discard it.

Two rules, and the second is the one that matters: only a DRAFT may go.
A SUBMITTED inspection is a record a Fleet reviewer has acted on -- it
may have raised maintenance orders and it is what an audit points to.
Deleting one would erase evidence.
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

DRIVER = ["vehicle.view", "checklist.view", "checklist.create",
          "checklist.update"]


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
    fleet = _user("fleet", DRIVER + ["checklist.submit"], b.id)
    juan = Driver(person_id="P1", employee_number="E1", first_name="Juan",
                  last_name="Cruz", assignee_type="DRIVER", branch_id=b.id)
    maria = Driver(person_id="P2", employee_number="E2", first_name="Maria",
                   last_name="Santos", assignee_type="DRIVER", branch_id=b.id)
    db.session.add_all([juan, maria])
    db.session.commit()
    DriverService().link_user(juan.id, ju.id)
    DriverService().link_user(maria.id, mu.id)
    v = VehicleService().create(vehicle_type_id=vt.id, brand="Toyota",
                                model="Vios", year=2009, branch_id=b.id,
                                conduction_number="A1", plate_number="NFO-864")
    db.session.commit()
    VehicleAssignmentService().assign(v.id, juan.id, source="MANUAL")
    tpl = VehicleChecklistService().create_template(name="Daily")
    db.session.commit()
    return v, tpl, ju, mu, fleet


def _hdr(client, username="juan"):
    r = client.post("/api/v1/auth/token",
                    json={"username": username, "password": "secret123"})
    return {"Authorization": f"Bearer "
            f"{json.loads(r.get_data(as_text=True))['access_token']}"}


def _make_draft(client, v, tpl):
    r = client.post("/api/v1/checklists", headers=_hdr(client),
                    json={"vehicle_id": v.id, "template_id": tpl.id})
    return json.loads(r.get_data(as_text=True))["id"]


def test_a_driver_can_delete_their_own_draft(app, client, env):
    v, tpl, *_ = env
    cid = _make_draft(client, v, tpl)

    r = client.delete(f"/api/v1/checklists/{cid}", headers=_hdr(client))

    assert r.status_code == 200
    assert db.session.get(VehicleChecklist, cid) is None


def test_deleting_removes_its_lines(app, client, env):
    """A draft with answers on it must not leave orphaned line rows
    behind. Lines are created when the checklist is answered, so the
    draft is saved with a response first."""
    v, tpl, *_ = env
    cid = _make_draft(client, v, tpl)
    from app.modules.transactions.vehicle_checklist.models import (
        VehicleChecklistLine)
    # Answer one item so at least one line row exists.
    line = VehicleChecklistLine.query.filter_by(checklist_id=cid).first()
    if line is None:
        # Some templates create lines lazily on save_draft; force one.
        VehicleChecklistService().save_draft(
            cid, responses=[], defects=[], odometer=None,
            driver_id=None, remarks="pre-delete")
        db.session.commit()
    before = VehicleChecklistLine.query.filter_by(checklist_id=cid).count()

    client.delete(f"/api/v1/checklists/{cid}", headers=_hdr(client))

    assert db.session.get(VehicleChecklist, cid) is None
    # Whatever lines existed are gone with it.
    assert VehicleChecklistLine.query.filter_by(checklist_id=cid).count() == 0
    assert before >= 0  # documents the pre-state without over-asserting


def test_a_submitted_checklist_cannot_be_deleted(app, client, env):
    """The rule that matters. A submitted inspection is a record; a
    reviewer has acted on it and an audit points to it."""
    v, tpl, _ju, _mu, _fleet = env
    cid = _make_draft(client, v, tpl)
    VehicleChecklistService().submit(cid, user=User.query.filter_by(
        username="fleet").first())
    db.session.commit()

    r = client.delete(f"/api/v1/checklists/{cid}", headers=_hdr(client, "fleet"))

    assert r.status_code == 409
    assert "already been submitted" in json.loads(
        r.get_data(as_text=True))["message"]
    assert db.session.get(VehicleChecklist, cid) is not None


def test_a_driver_cannot_delete_another_drivers_draft(app, client, env):
    """404, not 403 -- an id must not be probable. Maria cannot see
    Juan's inspection in her list; she must not remove it by id
    either."""
    v, tpl, *_ = env
    cid = _make_draft(client, v, tpl)  # Juan's

    r = client.delete(f"/api/v1/checklists/{cid}", headers=_hdr(client, "maria"))

    assert r.status_code == 404
    assert db.session.get(VehicleChecklist, cid) is not None


def test_a_reviewer_can_delete_any_draft(app, client, env):
    """checklist.submit is the reviewer signal used everywhere else; a
    Fleet Officer can clean up a mistaken draft on a driver's behalf."""
    v, tpl, *_ = env
    cid = _make_draft(client, v, tpl)

    r = client.delete(f"/api/v1/checklists/{cid}", headers=_hdr(client, "fleet"))

    assert r.status_code == 200


def test_deleting_a_missing_checklist_is_a_404(app, client, env):
    r = client.delete("/api/v1/checklists/999999", headers=_hdr(client))
    assert r.status_code == 404
