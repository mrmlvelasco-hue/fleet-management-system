"""Tire and Battery transactions surface the shared `display_status`.

Unlike Purchase Request/ATD/Maintenance Order, this module's own
approve() already moves the stored `status` straight to "COMPLETED"
the moment the approval instance reaches APPROVED (it applies the
physical effect right then too) -- there is no "approved but not yet
closed out" gap to paper over here. What WAS missing is the field
itself: while a transaction sits DRAFT-and-submitted, waiting on an
approver, the payload had no way to say so beyond the bare "DRAFT"
status, identical to a transaction nobody had even submitted yet.

One serialiser (`_txn_json`) backs both tire and battery transactions,
so one test per asset type is enough to prove the field reaches both.
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
    from app.modules.approval_config.models import (
        ApprovalLevel, ApprovalMatrix, ApprovalPath)

    for code, name in (("TIR", "Tire Transaction"),
                       ("BAT", "Battery Transaction")):
        dt = DocumentType.query.filter_by(code=code).first()
        if dt is None:
            dt = DocumentType(code=code, name=name, requires_approval=True)
            db.session.add(dt)
        else:
            dt.requires_approval = True
        db.session.flush()
        # An approval matrix, or submit refuses with "no matrix
        # configured".
        path = ApprovalPath(name=f"{code} Path")
        db.session.add(path)
        db.session.flush()
        db.session.add(ApprovalMatrix(document_type_id=dt.id,
                                      min_amount=None, max_amount=None,
                                      approval_path_id=path.id))
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


def test_an_unsubmitted_draft_still_shows_as_draft(app, client, env):
    tire, _u = env
    txn = _create(client, tire)

    body = _body(client.get(f"/api/v1/tire-transactions/{txn['id']}",
                            headers=_hdr(client)))

    assert body["status"] == "DRAFT"
    assert body["display_status"] == "Draft"


def test_a_submitted_tire_txn_awaiting_approval_says_so(app, client, env):
    """THE gap. The stored status stays "DRAFT" from submission until
    the approver acts -- without the resolver, a submitted transaction
    waiting on an approver looked identical to one nobody had even
    submitted yet."""
    tire, _u = env
    txn = _create(client, tire)

    r = client.post(f"/api/v1/tire-transactions/{txn['id']}/submit",
                    headers=_hdr(client), json={})
    assert r.status_code == 200, _body(r)

    body = _body(client.get(f"/api/v1/tire-transactions/{txn['id']}",
                            headers=_hdr(client)))

    assert body["status"] == "DRAFT"
    assert body["display_status"] == "Awaiting Approval"


def test_battery_transaction_reports_the_same_field(app, client, env):
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

    assert body["display_status"] == "Draft"
