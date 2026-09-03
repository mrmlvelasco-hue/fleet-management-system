"""Documents (J4) and ATDs in the assignee namespace.

The ATD rule is deliberately STRICTER than the vehicle rule: the client
decided the ATD in the app is for the signed-in assignee and nothing
else, so it filters on the named driver rather than on the assigned
vehicle. The test that matters is the one where the caller holds the
vehicle and still cannot see somebody else's authority to drive it.
"""
import io
import json
from datetime import date

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
from app.modules.transactions.atd.models import AuthorityToDrive
from app.modules.user_management.models import Permission, Role, User

CODES = ["vehicle.view", "vehicle.update", "atd.view"]


def _perm(code):
    p = Permission.query.filter_by(code=code).first()
    if p is None:
        module, action = code.split(".")
        p = Permission(code=code, module=module, action=action)
        db.session.add(p)
        db.session.flush()
    return p


def _user(username, branch_id):
    role = Role(name=f"r-{username}", description="r")
    db.session.add(role)
    db.session.flush()
    for c in CODES:
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
    ju, mu = _user("juan", b.id), _user("maria", b.id)
    juan = Driver(person_id="PID-1", employee_number="EMP-001",
                  first_name="Juan", last_name="Cruz",
                  assignee_type="DRIVER", branch_id=b.id)
    maria = Driver(person_id="PID-2", employee_number="EMP-002",
                   first_name="Maria", last_name="Santos",
                   assignee_type="DRIVER", branch_id=b.id)
    db.session.add_all([juan, maria])
    db.session.commit()
    DriverService().link_user(juan.id, ju.id)
    DriverService().link_user(maria.id, mu.id)
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
    return b, ju, mu, juan, maria, mine, theirs


def _hdr(client, username="juan"):
    r = client.post("/api/v1/auth/token",
                    json={"username": username, "password": "secret123"})
    return {"Authorization": f"Bearer "
            f"{json.loads(r.get_data(as_text=True))['access_token']}"}


def _body(r):
    return json.loads(r.get_data(as_text=True))


def _upload(vehicle_id, name="cr.pdf"):
    from werkzeug.datastructures import FileStorage
    from app.core.attachments.attachment_service import AttachmentService
    fs = FileStorage(stream=io.BytesIO(b"%PDF-1.4 fake"), filename=name,
                     content_type="application/pdf")
    att = AttachmentService().upload(fs, "vehicles", vehicle_id)
    db.session.commit()
    return att


# ── documents (J4) ───────────────────────────────────────────────────

def test_documents_listed_for_my_vehicle(app, client, env):
    _b, _ju, _mu, _j, _m, mine, _theirs = env
    _upload(mine.id)

    r = client.get(f"/api/v1/my/vehicles/{mine.id}/documents",
                   headers=_hdr(client))

    assert r.status_code == 200
    assert _body(r)["total"] == 1


def test_documents_404_for_a_branchmates_vehicle(app, client, env):
    """J4. Same branch, so org scope would have allowed this."""
    _b, _ju, _mu, _j, _m, _mine, theirs = env
    _upload(theirs.id)

    r = client.get(f"/api/v1/my/vehicles/{theirs.id}/documents",
                   headers=_hdr(client))

    assert r.status_code == 404


def test_download_works_for_my_document(app, client, env):
    _b, _ju, _mu, _j, _m, mine, _theirs = env
    att = _upload(mine.id)

    r = client.get(
        f"/api/v1/my/vehicles/{mine.id}/documents/{att.id}/download",
        headers=_hdr(client))

    assert r.status_code == 200


def test_download_404_for_a_branchmates_document(app, client, env):
    _b, _ju, _mu, _j, _m, _mine, theirs = env
    att = _upload(theirs.id)

    r = client.get(
        f"/api/v1/my/vehicles/{theirs.id}/documents/{att.id}/download",
        headers=_hdr(client))

    assert r.status_code == 404


def test_cannot_fetch_another_vehicles_document_through_my_own(app, client,
                                                                env):
    """The id-walk. Holding one vehicle must not become a way to read
    every attachment in the system by pairing my vehicle id with
    somebody else's attachment id."""
    _b, _ju, _mu, _j, _m, mine, theirs = env
    other_att = _upload(theirs.id)

    r = client.get(
        f"/api/v1/my/vehicles/{mine.id}/documents/{other_att.id}/download",
        headers=_hdr(client))

    assert r.status_code == 404


# ── ATDs ─────────────────────────────────────────────────────────────

def _atd(vehicle_id, driver_id, number):
    a = AuthorityToDrive(vehicle_id=vehicle_id, driver_id=driver_id,
                         document_number=number, status="ACTIVE",
                         purpose="Delivery run",
                         valid_from=date(2026, 9, 1),
                         valid_to=date(2026, 9, 30))
    db.session.add(a)
    db.session.commit()
    return a


def test_my_atds_lists_mine(app, client, env):
    _b, _ju, _mu, juan, _m, mine, _theirs = env
    _atd(mine.id, juan.id, "ATD-2026-000001")

    r = client.get("/api/v1/my/atds", headers=_hdr(client))

    assert r.status_code == 200
    assert [a["document_number"] for a in _body(r)["items"]] == [
        "ATD-2026-000001"]


def test_an_atd_on_my_vehicle_issued_to_someone_else_is_hidden(app, client,
                                                                env):
    """The client's rule, and the sharpest version of it.

    Juan currently HOLDS this vehicle. The ATD is Maria's authority to
    drive it. Filtering on the vehicle would show it; filtering on the
    named driver does not -- and the named driver is what was asked
    for, because an ATD is a document about a person's authority rather
    than about the vehicle."""
    _b, _ju, _mu, _juan, maria, mine, _theirs = env
    _atd(mine.id, maria.id, "ATD-2026-000002")

    r = client.get("/api/v1/my/atds", headers=_hdr(client))

    assert _body(r)["items"] == []


def test_atd_detail_404_for_someone_elses(app, client, env):
    _b, _ju, _mu, _juan, maria, mine, _theirs = env
    a = _atd(mine.id, maria.id, "ATD-2026-000003")

    r = client.get(f"/api/v1/my/atds/{a.id}", headers=_hdr(client))

    assert r.status_code == 404


def test_atd_detail_works_for_mine(app, client, env):
    _b, _ju, _mu, juan, _m, mine, _theirs = env
    a = _atd(mine.id, juan.id, "ATD-2026-000004")

    r = client.get(f"/api/v1/my/atds/{a.id}", headers=_hdr(client))

    assert r.status_code == 200
    assert _body(r)["document_number"] == "ATD-2026-000004"


def test_unlinked_user_sees_no_atds(app, client, env):
    b, _ju, _mu, juan, _m, mine, _theirs = env
    _atd(mine.id, juan.id, "ATD-2026-000005")
    _user("stranger", b.id)

    r = client.get("/api/v1/my/atds", headers=_hdr(client, "stranger"))

    assert r.status_code == 200
    assert _body(r)["items"] == []


def test_an_atd_for_a_vehicle_NOT_assigned_to_me_still_appears(app, client,
                                                                env):
    """An ATD may cover a vehicle the holder is not assigned.

    A pool vehicle, a one-off delivery, a temporary authority while
    their own unit is in for maintenance -- the ATD IS the authority, so
    it must not be filtered by whether that vehicle happens to be
    assigned to them. Juan is assigned MINE-111; this ATD is his
    authority over HERS-222, and he must be able to see it.
    """
    _b, _ju, _mu, juan, _maria, _mine, theirs = env
    _atd(theirs.id, juan.id, "ATD-2026-000010")

    r = client.get("/api/v1/my/atds", headers=_hdr(client))

    numbers = [a["document_number"] for a in _body(r)["items"]]
    assert "ATD-2026-000010" in numbers


def test_detail_of_an_atd_on_an_unassigned_vehicle_is_readable(app, client,
                                                                env):
    _b, _ju, _mu, juan, _maria, _mine, theirs = env
    a = _atd(theirs.id, juan.id, "ATD-2026-000011")

    r = client.get(f"/api/v1/my/atds/{a.id}", headers=_hdr(client))

    assert r.status_code == 200
    assert _body(r)["vehicle_id"] == theirs.id


def test_the_atd_payload_identifies_the_vehicle(app, client, env):
    """If an ATD can cover a vehicle other than the one assigned, the
    payload MUST say which vehicle -- otherwise a driver holding two
    authorities cannot tell them apart, and an enforcer asking to see
    the authority for a specific plate gets a document that names no
    plate."""
    _b, _ju, _mu, juan, _maria, _mine, theirs = env
    _atd(theirs.id, juan.id, "ATD-2026-000012")

    row = _body(client.get("/api/v1/my/atds", headers=_hdr(client)))["items"][0]

    assert row["plate_number"] == "HERS-222"
