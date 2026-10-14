"""Mobile: a focused Maintenance Orders panel on the assignee's vehicle.

Michael, 2026-10-14, with mockup: a card on My Vehicle that lists every
MO on this unit with its *display status* (the same label the desktop
MO list uses -- "For Approval", "In Progress", "Approved", "Draft",
"Completed", "Cancelled"), who the approval is currently pending with,
and -- when a Purchase Request was raised off the order -- the PR
nested underneath it so it reads as "because of this MO", not as a
separate unrelated row.

A specific endpoint, not a slice of a generic one: the desktop MO list
already serializes this shape, so this endpoint reuses the exact same
labels (_display_status, _approval_chain) instead of restating them.
"""
import json
from datetime import date

import pytest

from app.core.approval.models import ApprovalAction, ApprovalInstance
from app.core.security.password import hash_password
from app.extensions import db
from app.modules.approval_config.models import (
    ApprovalLevel, ApprovalMatrix, ApprovalPath)
from app.modules.document_config.models import DocumentType
from app.modules.document_config.service import DocumentTypeService
from app.modules.master_data.driver.models import Driver
from app.modules.master_data.driver.service import DriverService
from app.modules.master_data.org.models import Branch
from app.modules.master_data.reference.models import VehicleType
from app.modules.master_data.vehicle.assignment_service import (
    VehicleAssignmentService)
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.transactions.maintenance_order.models import MaintenanceOrder
from app.modules.transactions.purchase_request.models import PurchaseRequest
from app.modules.user_management.models import Permission, Role, User


def _perm(code):
    p = Permission.query.filter_by(code=code).first()
    if p is None:
        module, action = code.split(".")
        p = Permission(code=code, module=module, action=action)
        db.session.add(p); db.session.flush()
    return p


def _user(username, branch_id, codes=("vehicle.view",)):
    role = Role(name=f"r-{username}", description="r")
    db.session.add(role); db.session.flush()
    for c in codes:
        role.permissions.append(_perm(c))
    u = User(username=username, email=f"{username}@example.com",
             password_hash=hash_password("secret123"), is_active=True,
             branch_id=branch_id, mobile_access=True)
    u.roles.append(role)
    db.session.add(u); db.session.commit()
    return u


@pytest.fixture()
def env(app):
    b = Branch(code="MMO", name="MMO Branch")
    vt = VehicleType(code="LT-MMO", name="Light", category="LIGHT")
    db.session.add_all([b, vt]); db.session.commit()

    if DocumentType.query.filter_by(code="MO").first() is None:
        DocumentTypeService().create(code="MO", name="MO",
                                     requires_approval=True,
                                     auto_numbering=False)

    juan_u = _user("mmo_juan", b.id)
    maria_u = _user("mmo_maria", b.id)
    approver_u = _user("mmo_bmgr", b.id, codes=("vehicle.view",))

    juan = Driver(person_id="PID-MMO-1", employee_number="EMP-MMO-1",
                  first_name="Juan", last_name="Cruz",
                  assignee_type="DRIVER", branch_id=b.id)
    maria = Driver(person_id="PID-MMO-2", employee_number="EMP-MMO-2",
                   first_name="Maria", last_name="Santos",
                   assignee_type="DRIVER", branch_id=b.id)
    db.session.add_all([juan, maria]); db.session.commit()
    DriverService().link_user(juan.id, juan_u.id)
    DriverService().link_user(maria.id, maria_u.id)

    appr_role = Role(name="Branch Manager", description="appr")
    db.session.add(appr_role); db.session.flush()
    appr_role.permissions.append(_perm("vehicle.view"))
    approver_u.roles.append(appr_role)
    db.session.commit()

    mine = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2009,
        branch_id=b.id, conduction_number="NZO-A", plate_number="NZO-629")
    theirs = VehicleService().create(
        vehicle_type_id=vt.id, brand="Isuzu", model="DMax", year=2023,
        branch_id=b.id, conduction_number="NZO-B", plate_number="HERS-629")
    db.session.commit()

    VehicleAssignmentService().assign(mine.id, juan.id, source="MANUAL")
    VehicleAssignmentService().assign(theirs.id, maria.id, source="MANUAL")

    path = ApprovalPath(name="MMO path")
    db.session.add(path); db.session.flush()
    db.session.add(ApprovalLevel(path_id=path.id, level_number=1,
                                 approver_type="ROLE", role_id=appr_role.id))
    dt = DocumentType.query.filter_by(code="MO").first()
    db.session.add(ApprovalMatrix(document_type_id=dt.id,
                                  approval_path_id=path.id))
    db.session.commit()

    return dict(branch=b, vt=vt, juan=juan_u, maria=maria_u,
                approver=approver_u, mine=mine, theirs=theirs,
                juan_driver=juan, path=path, dt=dt)


def _hdr(client, username="mmo_juan"):
    r = client.post("/api/v1/auth/token",
                    json={"username": username, "password": "secret123"})
    tok = json.loads(r.get_data(as_text=True))["access_token"]
    return {"Authorization": f"Bearer {tok}"}


def _body(r):
    return json.loads(r.get_data(as_text=True))


def _mo(env, *, status="DRAFT", number="MO-2026-00001", description="Repair",
        with_pending_approval=False, approved=False):
    mo = MaintenanceOrder(document_number=number, vehicle_id=env["mine"].id,
                          order_category="MAINTENANCE",
                          scheduled_date=date(2026, 10, 6),
                          description=description, status=status)
    db.session.add(mo); db.session.flush()
    if with_pending_approval or approved:
        inst = ApprovalInstance(
            document_type_id=env["dt"].id, approval_path_id=env["path"].id,
            reference_table="maintenance_orders", reference_id=mo.id,
            status="APPROVED" if approved else "PENDING",
            current_level=1, submitted_by=env["juan"].id)
        db.session.add(inst); db.session.flush()
        db.session.add(ApprovalAction(
            instance_id=inst.id, level_number=1, action="SUBMIT",
            acted_by=env["juan"].id))
        if approved:
            db.session.add(ApprovalAction(
                instance_id=inst.id, level_number=1, action="APPROVE",
                acted_by=env["approver"].id))
        mo.approval_instance_id = inst.id
    db.session.commit()
    return mo


def _pr(env, mo, *, number="PR-2026-00001", status="DRAFT"):
    pr = PurchaseRequest(document_number=number, department_id=1,
                        requested_by=env["juan"].id,
                        amount=5000, description="Brake pads",
                        status=status)
    db.session.add(pr); db.session.flush()
    mo.purchase_request_id = pr.id
    db.session.commit()
    return pr


# ── scope (same shape as the rest of /my/*) ────────────────────────

def test_404_for_a_vehicle_the_caller_does_not_hold(client, env):
    r = client.get(
        f"/api/v1/my/vehicles/{env['theirs'].id}/maintenance-orders",
        headers=_hdr(client))
    assert r.status_code == 404


def test_empty_items_when_nothing_is_in_flight(client, env):
    body = _body(client.get(
        f"/api/v1/my/vehicles/{env['mine'].id}/maintenance-orders",
        headers=_hdr(client)))
    assert body == {"items": [], "total": 0, "active_count": 0}


# ── display status matches the desktop MO list ──────────────────────

def test_draft_mo_reads_as_draft(client, env):
    _mo(env, status="DRAFT", number="MO-D-1", description="Fix wipers")
    item = _body(client.get(
        f"/api/v1/my/vehicles/{env['mine'].id}/maintenance-orders",
        headers=_hdr(client)))["items"][0]
    assert item["display_status"] == "Draft"
    assert item["status"] == "DRAFT"
    assert item["approval_status"] is None


def test_pending_mo_reads_as_awaiting_approval_and_names_the_approver(
        client, env):
    _mo(env, status="DRAFT", number="MO-P-1", description="PMS 40,000 km",
        with_pending_approval=True)
    item = _body(client.get(
        f"/api/v1/my/vehicles/{env['mine'].id}/maintenance-orders",
        headers=_hdr(client)))["items"][0]
    assert item["display_status"] == "Awaiting Approval"
    assert item["approval_status"] == "PENDING"
    assert item["pending_with"] == "Branch Manager"


def test_approved_draft_reads_as_approved(client, env):
    _mo(env, status="DRAFT", number="MO-A-1", description="Replace plugs",
        approved=True)
    item = _body(client.get(
        f"/api/v1/my/vehicles/{env['mine'].id}/maintenance-orders",
        headers=_hdr(client)))["items"][0]
    assert item["display_status"] == "Approved"
    assert item["approval_status"] == "APPROVED"
    # No one is pending any more.
    assert item["pending_with"] is None


def test_in_progress_mo_reads_as_in_progress(client, env):
    _mo(env, status="IN_PROGRESS", number="MO-IP-1",
        description="Corrective — Brake System", approved=True)
    item = _body(client.get(
        f"/api/v1/my/vehicles/{env['mine'].id}/maintenance-orders",
        headers=_hdr(client)))["items"][0]
    assert item["display_status"] == "In Progress"


def test_completed_mo_is_listed_too_so_history_survives(client, env):
    """The mockup's 'View all maintenance history' link needs the
    completed rows for the vehicle too -- the panel decides how many to
    show on screen, the endpoint returns what's there."""
    _mo(env, status="COMPLETED", number="MO-C-1", description="PMS 30k",
        approved=True)
    item = _body(client.get(
        f"/api/v1/my/vehicles/{env['mine'].id}/maintenance-orders",
        headers=_hdr(client)))["items"][0]
    assert item["display_status"] == "Completed"


def test_cancelled_mo_is_listed_with_the_cancelled_label(client, env):
    _mo(env, status="CANCELLED", number="MO-X-1", description="Scrapped")
    item = _body(client.get(
        f"/api/v1/my/vehicles/{env['mine'].id}/maintenance-orders",
        headers=_hdr(client)))["items"][0]
    assert item["display_status"] == "Cancelled"


# ── PR nested under its parent MO ───────────────────────────────────

def test_a_linked_purchase_request_is_nested_under_its_mo(client, env):
    mo = _mo(env, status="IN_PROGRESS", number="MO-WITH-PR",
             description="Brakes", approved=True)
    _pr(env, mo, number="PR-2026-00187", status="APPROVED")
    item = _body(client.get(
        f"/api/v1/my/vehicles/{env['mine'].id}/maintenance-orders",
        headers=_hdr(client)))["items"][0]
    assert item["purchase_request"] is not None
    assert item["purchase_request"]["document_number"] == "PR-2026-00187"
    assert item["purchase_request"]["status"] == "APPROVED"
    assert item["purchase_request"]["display_status"] == "Approved"


def test_a_mo_without_a_pr_has_no_purchase_request_key_value(client, env):
    _mo(env, status="DRAFT", number="MO-NO-PR", description="Inspection")
    item = _body(client.get(
        f"/api/v1/my/vehicles/{env['mine'].id}/maintenance-orders",
        headers=_hdr(client)))["items"][0]
    assert item["purchase_request"] is None


# ── ordering: in-flight first, then by scheduled date ───────────────

def test_in_flight_orders_come_before_completed_ones(client, env):
    _mo(env, status="COMPLETED", number="MO-OLD", description="old")
    _mo(env, status="DRAFT", number="MO-NEW", description="new",
        with_pending_approval=True)
    body = _body(client.get(
        f"/api/v1/my/vehicles/{env['mine'].id}/maintenance-orders",
        headers=_hdr(client)))
    assert [i["document_number"] for i in body["items"]] == \
        ["MO-NEW", "MO-OLD"]
    # The panel's "N active" count is in-flight only (not closed).
    assert body["active_count"] == 1


def test_an_order_on_a_bystanders_vehicle_is_not_mine(client, env):
    _mo(env, status="DRAFT", number="MO-NOPE", description="not mine")
    # Move it to theirs.
    MaintenanceOrder.query.filter_by(document_number="MO-NOPE").update(
        {"vehicle_id": env["theirs"].id})
    db.session.commit()
    body = _body(client.get(
        f"/api/v1/my/vehicles/{env['mine'].id}/maintenance-orders",
        headers=_hdr(client)))
    assert body["items"] == []
