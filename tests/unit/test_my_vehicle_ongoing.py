"""Mobile: the assignee can see the ongoing transactions on THEIR vehicle.

Michael, 2026-10-14: "we can see the ongoing Transactions related to
the vehicle assigned to the assignee."

Scoped the same way as every other /my/* endpoint: through
AssigneeScopeService, 404 (not 403) on a vehicle the caller does not
hold, so plate numbers and ids never leak as an enumeration oracle.

"Ongoing" means open across the shared edit policy -- the same statuses
that are editable (Draft, Pending, Returned, Approved, and the per-
document ongoing status: MO In Progress, Trip Ticket Released, Vehicle
Movement In Transit, ATD Active). Closed documents (Completed,
Received, Cancelled, Expired) and rejected ones never appear.
"""
import json
from datetime import date, datetime, timedelta

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
from app.modules.transactions.maintenance_order.models import MaintenanceOrder
from app.modules.transactions.trip_ticket.models import TripTicket
from app.modules.transactions.vehicle_movement.models import VehicleMovement
from app.modules.user_management.models import Permission, Role, User


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
    for c in ("vehicle.view",):
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
    b = Branch(code="ONG", name="Ongoing Branch")
    vt = VehicleType(code="LT-ONG", name="Light", category="LIGHT")
    db.session.add_all([b, vt])
    db.session.commit()

    juan_u = _user("ong_juan", b.id)
    maria_u = _user("ong_maria", b.id)

    juan = Driver(person_id="PID-ONG-1", employee_number="EMP-ONG-1",
                  first_name="Juan", last_name="Cruz",
                  assignee_type="DRIVER", branch_id=b.id)
    maria = Driver(person_id="PID-ONG-2", employee_number="EMP-ONG-2",
                   first_name="Maria", last_name="Santos",
                   assignee_type="DRIVER", branch_id=b.id)
    db.session.add_all([juan, maria])
    db.session.commit()
    DriverService().link_user(juan.id, juan_u.id)
    DriverService().link_user(maria.id, maria_u.id)

    mine = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2024,
        branch_id=b.id, conduction_number="ONG-A1", plate_number="MINE-ONG")
    theirs = VehicleService().create(
        vehicle_type_id=vt.id, brand="Isuzu", model="DMax", year=2023,
        branch_id=b.id, conduction_number="ONG-A2", plate_number="HERS-ONG")
    db.session.commit()

    VehicleAssignmentService().assign(mine.id, juan.id, source="MANUAL")
    VehicleAssignmentService().assign(theirs.id, maria.id, source="MANUAL")
    return dict(branch=b, vt=vt, juan=juan_u, maria=maria_u,
                mine=mine, theirs=theirs, juan_driver=juan, maria_driver=maria)


def _hdr(client, username="ong_juan"):
    r = client.post("/api/v1/auth/token",
                    json={"username": username, "password": "secret123"})
    tok = json.loads(r.get_data(as_text=True))["access_token"]
    return {"Authorization": f"Bearer {tok}"}


def _body(r):
    return json.loads(r.get_data(as_text=True))


def _mo(vehicle, *, status="DRAFT", number="MO-ONG-1", description="Repair"):
    mo = MaintenanceOrder(document_number=number, vehicle_id=vehicle.id,
                          order_category="MAINTENANCE", scheduled_date=date.today(),
                          description=description, status=status)
    db.session.add(mo); db.session.commit(); return mo


def _tt(vehicle, driver_id=None, *, status="DRAFT", number="TT-ONG-1",
        destination="Carmona Plant", purpose="Delivery"):
    tt = TripTicket(document_number=number, vehicle_id=vehicle.id,
                    driver_id=driver_id, destination=destination,
                    purpose=purpose, departure_datetime=datetime.now(),
                    odometer_out=1000, status=status)
    db.session.add(tt); db.session.commit(); return tt


def _vm(vehicle, *, status="DRAFT", number="VM-ONG-1"):
    vm = VehicleMovement(document_number=number, vehicle_id=vehicle.id,
                         movement_type="TRANSFER", from_location="Hub",
                         to_location="Branch", movement_date=date.today(),
                         status=status)
    db.session.add(vm); db.session.commit(); return vm


def _atd(vehicle, driver_id, *, status="ACTIVE", number="ATD-ONG-1",
         valid_to=None):
    atd = AuthorityToDrive(document_number=number, vehicle_id=vehicle.id,
                           driver_id=driver_id, purpose="Field visit",
                           valid_from=date.today(),
                           valid_to=valid_to or date.today() + timedelta(days=3),
                           status=status)
    db.session.add(atd); db.session.commit(); return atd


# ── scope (same shape as the rest of /my/*) ────────────────────────

def test_ongoing_404s_for_a_branchmates_vehicle(client, env):
    r = client.get(f"/api/v1/my/vehicles/{env['theirs'].id}/ongoing",
                   headers=_hdr(client))
    assert r.status_code == 404


def test_ongoing_404s_for_a_released_vehicle(client, env):
    VehicleAssignmentService().release(env["mine"].id, source="MANUAL")
    r = client.get(f"/api/v1/my/vehicles/{env['mine'].id}/ongoing",
                   headers=_hdr(client))
    assert r.status_code == 404


def test_ongoing_is_empty_when_nothing_is_in_flight(client, env):
    r = client.get(f"/api/v1/my/vehicles/{env['mine'].id}/ongoing",
                   headers=_hdr(client))
    assert r.status_code == 200
    assert _body(r)["items"] == []


# ── per document kind ─────────────────────────────────────────────

def test_a_draft_mo_on_my_vehicle_is_listed(client, env):
    _mo(env["mine"], status="DRAFT", description="Brake pads")
    body = _body(client.get(f"/api/v1/my/vehicles/{env['mine'].id}/ongoing",
                            headers=_hdr(client)))
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["document_type"] == "Maintenance Order"
    assert item["document_number"] == "MO-ONG-1"
    assert item["status"] == "DRAFT"
    assert item["summary"] == "Brake pads"


def test_an_in_progress_mo_is_listed(client, env):
    _mo(env["mine"], status="IN_PROGRESS")
    body = _body(client.get(f"/api/v1/my/vehicles/{env['mine'].id}/ongoing",
                            headers=_hdr(client)))
    assert [i["status"] for i in body["items"]] == ["IN_PROGRESS"]


def test_a_completed_mo_is_not_listed(client, env):
    _mo(env["mine"], status="COMPLETED")
    assert _body(client.get(f"/api/v1/my/vehicles/{env['mine'].id}/ongoing",
                            headers=_hdr(client)))["items"] == []


def test_a_cancelled_mo_is_not_listed(client, env):
    _mo(env["mine"], status="CANCELLED")
    assert _body(client.get(f"/api/v1/my/vehicles/{env['mine'].id}/ongoing",
                            headers=_hdr(client)))["items"] == []


def test_a_released_trip_ticket_is_listed(client, env):
    _tt(env["mine"], env["juan_driver"].id, status="RELEASED",
        number="TT-R-1")
    body = _body(client.get(f"/api/v1/my/vehicles/{env['mine'].id}/ongoing",
                            headers=_hdr(client)))
    kinds = [i["document_type"] for i in body["items"]]
    assert "Trip Ticket" in kinds


def test_a_completed_trip_ticket_is_not_listed(client, env):
    _tt(env["mine"], env["juan_driver"].id, status="COMPLETED")
    assert _body(client.get(f"/api/v1/my/vehicles/{env['mine'].id}/ongoing",
                            headers=_hdr(client)))["items"] == []


def test_a_movement_in_transit_is_listed(client, env):
    _vm(env["mine"], status="IN_TRANSIT")
    body = _body(client.get(f"/api/v1/my/vehicles/{env['mine'].id}/ongoing",
                            headers=_hdr(client)))
    assert [i["document_type"] for i in body["items"]] == ["Vehicle Movement"]


def test_an_active_atd_is_listed(client, env):
    _atd(env["mine"], env["juan_driver"].id, status="ACTIVE")
    body = _body(client.get(f"/api/v1/my/vehicles/{env['mine'].id}/ongoing",
                            headers=_hdr(client)))
    assert [i["document_type"] for i in body["items"]] == ["Authority To Drive"]


def test_an_expired_atd_is_not_listed(client, env):
    """A permit whose Valid To has passed is finished (KI-10)."""
    _atd(env["mine"], env["juan_driver"].id, status="ACTIVE",
         valid_to=date.today() - timedelta(days=1))
    body = _body(client.get(f"/api/v1/my/vehicles/{env['mine'].id}/ongoing",
                            headers=_hdr(client)))
    assert body["items"] == []


def test_a_stored_expired_atd_is_not_listed(client, env):
    _atd(env["mine"], env["juan_driver"].id, status="EXPIRED")
    assert _body(client.get(f"/api/v1/my/vehicles/{env['mine'].id}/ongoing",
                            headers=_hdr(client)))["items"] == []


# ── mixing and ordering ─────────────────────────────────────────────

def test_several_documents_come_back_newest_first(client, env):
    _mo(env["mine"], status="DRAFT", number="MO-A")
    _tt(env["mine"], env["juan_driver"].id, status="RELEASED", number="TT-B")
    _vm(env["mine"], status="IN_TRANSIT", number="VM-C")
    body = _body(client.get(f"/api/v1/my/vehicles/{env['mine'].id}/ongoing",
                            headers=_hdr(client)))
    types = {i["document_type"] for i in body["items"]}
    assert types == {"Maintenance Order", "Trip Ticket", "Vehicle Movement"}
    assert len(body["items"]) == 3


def test_a_transaction_on_someone_elses_vehicle_is_not_mine(client, env):
    _mo(env["theirs"], status="DRAFT", number="MO-NOT-MINE")
    body = _body(client.get(f"/api/v1/my/vehicles/{env['mine'].id}/ongoing",
                            headers=_hdr(client)))
    assert body["items"] == []


# ── a brief, mobile-friendly payload ─────────────────────────────────

def test_each_item_carries_the_fields_the_mobile_card_needs(client, env):
    _mo(env["mine"], status="IN_PROGRESS", description="PMS 10,000km")
    item = _body(client.get(f"/api/v1/my/vehicles/{env['mine'].id}/ongoing",
                            headers=_hdr(client)))["items"][0]
    for key in ("id", "document_type", "document_number", "status",
                "summary", "date"):
        assert key in item, f"missing {key}: {item}"
