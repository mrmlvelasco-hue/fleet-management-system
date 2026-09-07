"""Tire and Battery transactions report their approval state.

Reported from the client's screen: after submitting a tire transaction
for approval, the page still offered "Submit for Approval" and "Cancel",
and the workflow panel still said "Not yet submitted for approval".

The submit itself worked. The DETAIL PAYLOAD carried only `status` --
no approval_instance_status, no has_approval_instance, no chain -- so
the screen had nothing to react to and kept rendering the pre-submit
state.

Every other transaction module reports these keys; this one never did.
"""
import json
from datetime import date

import pytest

from app.core.security.password import hash_password
from app.extensions import db
from app.modules.document_config.models import DocumentType
from app.modules.master_data.org.models import Branch
from app.modules.master_data.tire.models import Tire
from app.modules.user_management.models import Permission, Role, User


def _perm(code):
    p = Permission.query.filter_by(code=code).first()
    if p is None:
        m, a = code.split(".")
        p = Permission(code=code, module=m, action=a)
        db.session.add(p)
        db.session.flush()
    return p


@pytest.fixture()
def env(app):
    b = Branch(code="CAR", name="Carmona")
    db.session.add(b)
    db.session.commit()
    role = Role(name="R", description="r")
    db.session.add(role)
    db.session.flush()
    for c in ("tire.view", "tire.create", "tire.update",
              "battery.view", "battery.create", "battery.update"):
        role.permissions.append(_perm(c))
    u = User(username="admin", email="a@e.com",
             password_hash=hash_password("secret123"), is_active=True)
    u.roles.append(role)
    db.session.add(u)
    dt = DocumentType.query.filter_by(code="TIR").first()
    if dt is None:
        dt = DocumentType(code="TIR", name="Tire Transaction",
                          requires_approval=True)
        db.session.add(dt)
    else:
        dt.requires_approval = True
    db.session.flush()
    # An approval matrix, or submit refuses with "no matrix configured"
    # -- correct behaviour, but not what these tests are about.
    from app.modules.approval_config.models import (
        ApprovalMatrix, ApprovalPath)
    path = ApprovalPath(name="Asset Path")
    db.session.add(path)
    db.session.flush()
    db.session.add(ApprovalMatrix(document_type_id=dt.id, min_amount=None,
                                  max_amount=None,
                                  approval_path_id=path.id))
    # A path with no levels submits and then cannot route -- "Current
    # level not found on path."
    from app.modules.approval_config.models import ApprovalLevel
    db.session.add(ApprovalLevel(path_id=path.id, level_number=1,
                                 approver_type="ROLE", role_id=role.id))
    tire = Tire(serial_number="SN-123098", brand="Yokohama",
                size="185/65R15", tire_type="RADIAL", status="MOUNTED",
                branch_id=b.id)
    db.session.add(tire)
    db.session.commit()
    return tire, u


def _hdr(client):
    r = client.post("/api/v1/auth/token",
                    json={"username": "admin", "password": "secret123"})
    return {"Authorization": f"Bearer "
            f"{json.loads(r.get_data(as_text=True))['access_token']}"}


def _body(r):
    return json.loads(r.get_data(as_text=True))


def _create(client, tire):
    r = client.post("/api/v1/tire-transactions", headers=_hdr(client),
                    json={"tire_id": tire.id, "action": "DISMOUNT",
                          "transaction_date": str(date.today())})
    body = _body(r)
    assert r.status_code == 201, body
    return body


def test_a_fresh_draft_reports_no_approval_instance(app, client, env):
    tire, _u = env
    txn = _create(client, tire)

    body = _body(client.get(f"/api/v1/tire-transactions/{txn['id']}",
                            headers=_hdr(client)))

    assert body["has_approval_instance"] is False
    assert body["approval_instance_status"] is None
    assert body["approval_chain"] == []


def test_after_submitting_the_payload_says_so(app, client, env):
    """THE bug. The screen kept offering Submit because the payload
    never told it the document had moved."""
    tire, _u = env
    txn = _create(client, tire)

    r = client.post(f"/api/v1/tire-transactions/{txn['id']}/submit",
                    headers=_hdr(client), json={})
    assert r.status_code == 200, _body(r)
    body = _body(client.get(f"/api/v1/tire-transactions/{txn['id']}",
                            headers=_hdr(client)))

    assert body["has_approval_instance"] is True
    assert body["approval_instance_status"] == "PENDING"


def test_the_submit_response_itself_carries_the_new_state(app, client, env):
    """So the screen updates from the POST response without a refetch --
    which is what a user expects after pressing a button."""
    tire, _u = env
    txn = _create(client, tire)

    body = _body(client.post(
        f"/api/v1/tire-transactions/{txn['id']}/submit",
        headers=_hdr(client), json={}))

    assert body["has_approval_instance"] is True
    assert body["approval_instance_status"] == "PENDING"


def test_the_requester_is_identified(app, client, env):
    tire, _u = env
    txn = _create(client, tire)
    body = _body(client.get(f"/api/v1/tire-transactions/{txn['id']}",
                            headers=_hdr(client)))
    assert body["is_requester"] is True


def test_the_approval_chain_is_reported(app, client, env):
    """Built through ApprovalEngine, not hand-rolled -- the ATD print
    endpoint invented its own version from attributes that do not exist
    and printed an empty chain for months."""
    tire, _u = env
    txn = _create(client, tire)
    client.post(f"/api/v1/tire-transactions/{txn['id']}/submit",
                headers=_hdr(client), json={})

    body = _body(client.get(f"/api/v1/tire-transactions/{txn['id']}",
                            headers=_hdr(client)))

    assert isinstance(body["approval_chain"], list)


def test_battery_reports_the_same_keys(app, client, env):
    """One serialiser serves both modules; a fix to one that missed the
    other would be worse than no fix."""
    from app.modules.master_data.battery.models import Battery
    b = Branch.query.first()
    batt = Battery(serial_number="B-1", brand="Motolite", status="MOUNTED",
                   branch_id=b.id)
    db.session.add(batt)
    db.session.commit()

    txn = _body(client.post("/api/v1/battery-transactions",
                            headers=_hdr(client),
                            json={"battery_id": batt.id, "action": "DISMOUNT",
                                  "transaction_date": str(date.today())}))
    body = _body(client.get(f"/api/v1/battery-transactions/{txn['id']}",
                            headers=_hdr(client)))

    for key in ("has_approval_instance", "approval_instance_status",
                "can_act", "is_requester", "approval_chain"):
        assert key in body


def test_list_rows_stay_lean(app, client, env):
    """Approval state is detail-only. A list of 50 rows does not need a
    chain each, and building one would undo the query work done on
    these endpoints."""
    tire, _u = env
    _create(client, tire)
    rows = _body(client.get("/api/v1/tire-transactions",
                            headers=_hdr(client)))["items"]
    assert rows
    assert "approval_chain" not in rows[0]
