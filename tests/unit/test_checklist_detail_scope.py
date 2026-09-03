"""J3 -- the checklist detail IDOR.

`list_checklists` scopes to `created_by` for anyone lacking
`checklist.submit`. `GET /checklists/<id>` did not: it called a bare
db.session.get() with no scope check at all. So a driver who could no
longer LIST another driver's inspection could still fetch and edit it by
guessing the sequential id. The list scoping was the newer change and
the detail path was never updated with it.

404 rather than 403 on a miss. Inside a scoped read, "you may not see
this" and "this does not exist" must look identical, or sequential ids
become an oracle for how many inspections exist and when.
"""
import json

import pytest

from app.core.security.password import hash_password
from app.extensions import db
from app.modules.master_data.org.models import Branch
from app.modules.master_data.reference.models import VehicleType
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.transactions.vehicle_checklist.service import (
    VehicleChecklistService)
from app.modules.user_management.models import Permission, Role, User


def _perm(code):
    p = Permission.query.filter_by(code=code).first()
    if p is None:
        module, action = code.split(".")
        p = Permission(code=code, module=module, action=action)
        db.session.add(p)
        db.session.flush()
    return p


def _user(username, codes, branch_id=None):
    role = Role(name=f"role-{username}", description="r")
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
    v = VehicleService().create(vehicle_type_id=vt.id, brand="Toyota",
                                model="Hilux", year=2024, branch_id=b.id,
                                conduction_number="A-001",
                                plate_number="ABC-1234")
    db.session.commit()
    driver_codes = ["checklist.view", "checklist.create", "checklist.update"]
    juan = _user("juan", driver_codes, b.id)
    maria = _user("maria", driver_codes, b.id)
    reviewer = _user("reviewer", driver_codes + ["checklist.submit"], b.id)
    return b, vt, v, juan, maria, reviewer


def _tpl(vt_id):
    t = VehicleChecklistService().create_template(name="Daily")
    db.session.commit()
    return t


def _token(client, username):
    r = client.post("/api/v1/auth/token",
                    json={"username": username, "password": "secret123"})
    return json.loads(r.get_data(as_text=True))["access_token"]


def _hdr(client, username):
    return {"Authorization": f"Bearer {_token(client, username)}"}


@pytest.fixture()
def marias_checklist(env):
    _b, vt, v, _juan, maria, _rev = env
    t = _tpl(vt.id)
    cl = VehicleChecklistService().create(
        vehicle_id=v.id, template_id=t.id, user=maria)
    db.session.commit()
    return cl


def test_another_users_checklist_is_not_readable(app, client, env,
                                                  marias_checklist):
    """The IDOR itself. Juan cannot list Maria's inspection; he must
    not be able to fetch it by id either."""
    r = client.get(f"/api/v1/checklists/{marias_checklist.id}",
                   headers=_hdr(client, "juan"))
    assert r.status_code == 404


def test_another_users_checklist_is_not_writable(app, client, env,
                                                  marias_checklist):
    r = client.put(f"/api/v1/checklists/{marias_checklist.id}",
                   json={"remarks": "tampered"},
                   headers=_hdr(client, "juan"))
    assert r.status_code == 404
    assert db.session.get(
        type(marias_checklist), marias_checklist.id).remarks != "tampered"


def test_your_own_checklist_is_readable(app, client, env,
                                         marias_checklist):
    r = client.get(f"/api/v1/checklists/{marias_checklist.id}",
                   headers=_hdr(client, "maria"))
    assert r.status_code == 200


def test_your_own_checklist_is_writable(app, client, env,
                                         marias_checklist):
    r = client.put(f"/api/v1/checklists/{marias_checklist.id}",
                   json={"remarks": "wipers streaking"},
                   headers=_hdr(client, "maria"))
    assert r.status_code == 200


def test_a_reviewer_still_sees_every_checklist(app, client, env,
                                                marias_checklist):
    """The Fleet reviewer workflow must not break. checklist.submit is
    the existing signal for 'reviews everyone's inspections', and the
    detail rule has to match the list rule rather than invent its
    own."""
    r = client.get(f"/api/v1/checklists/{marias_checklist.id}",
                   headers=_hdr(client, "reviewer"))
    assert r.status_code == 200


def test_a_reviewer_may_still_edit(app, client, env, marias_checklist):
    r = client.put(f"/api/v1/checklists/{marias_checklist.id}",
                   json={"remarks": "verified"},
                   headers=_hdr(client, "reviewer"))
    assert r.status_code == 200


def test_a_missing_checklist_is_indistinguishable_from_a_hidden_one(
        app, client, env, marias_checklist):
    """Both 404. If a hidden checklist answered differently from a
    non-existent one, sequential ids would reveal how many inspections
    exist and when they were taken."""
    hidden = client.get(f"/api/v1/checklists/{marias_checklist.id}",
                        headers=_hdr(client, "juan"))
    absent = client.get("/api/v1/checklists/999999",
                        headers=_hdr(client, "juan"))
    assert hidden.status_code == absent.status_code == 404
