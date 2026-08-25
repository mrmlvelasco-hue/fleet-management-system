"""Maintenance Order list — stat summary, PR/Invoice, Docs, Requested By.

Built from a client mockup, checked against Flask FIRST rather than
implemented from the picture. Two things in that mockup are not backed
by real data and are deliberately NOT built here:

  * PRIORITY -- no column on MaintenanceOrder, no filter on Flask's own
    list route (`_txn_list_context`). Inventing one would be exactly
    the kind of hardcoded, undecided business rule the client's own
    spec says not to add.
  * The seven-tile split (Total / For Approval / Submitted / Approved /
    Draft / Completed / Rejected) treats "For Approval" and "Submitted"
    as two different states. The real vocabulary
    (ApprovalInstance.status: PENDING / APPROVED / REJECTED / RETURNED
    / CANCELLED) has no state matching one of those two labels -- they
    are almost certainly the same underlying PENDING state under two
    names, or one means "pending at MY level" and the other "pending
    anywhere". Guessing which would present an invented distinction as
    a real one. Built instead: separate, correctly-labelled counts for
    the MO's own PHYSICAL status (DRAFT/IN_PROGRESS/COMPLETED/
    CANCELLED) and its APPROVAL status (PENDING/APPROVED/REJECTED/
    RETURNED), which are two genuinely different facts the mockup
    collapsed into one flat list.

PR/Invoice and Docs ARE real: purchase_request_id already exists on the
model, invoices are already linked via backref, and Attachment is a
generic reference-table/reference-id model already used everywhere
else in the system.
"""
import json
from datetime import date

import pytest

from app.core.security.password import hash_password
from app.core.security.registry import sync_permissions
from app.modules.document_config.service import (
    DocumentTypeService, NumberingSchemeService)
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.reference.service import (
    MaintenanceTypeService, VehicleTypeService)
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.transactions.maintenance_order.service import (
    MaintenanceOrderService)
from app.modules.user_management.models import Permission, Role, User


def _ensure_doc_type(code):
    from app.modules.document_config.models import DocumentType
    if DocumentType.query.filter_by(code=code).first() is None:
        DocumentTypeService().create(code=code, name=code,
                                     requires_approval=False,
                                     auto_numbering=True)
        dt = DocumentType.query.filter_by(code=code).first()
        NumberingSchemeService().create(document_type_id=dt.id, prefix=code,
                                        include_year=True, digit_count=6,
                                        reset_policy="YEARLY")


@pytest.fixture()
def mol_env(db):
    sync_permissions()
    db.session.commit()

    role = Role(name="MO List Viewer")
    role.permissions = Permission.query.filter(
        Permission.code == "maintenanceorder.view").all()
    user = User(username="molviewer", email="ml@e.com",
                password_hash=hash_password("secret123"), is_active=True)
    user.roles = [role]
    db.session.add_all([role, user])

    branch = BranchService().create(code="BR-MOL", name="MOL Branch")
    vt = VehicleTypeService().create(code="LV-MOL", name="Light",
                                     category="LIGHT")
    mt = MaintenanceTypeService().create(code="MOL-MT", name="PMS",
                                         category="PM")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2024,
        branch_id=branch.id, conduction_number="MOL-000")
    _ensure_doc_type("MO")
    db.session.commit()
    return {"user": user, "branch": branch, "vt": vt, "mt": mt,
            "vehicle": vehicle}


def _token(client, username="molviewer"):
    r = client.post("/api/v1/auth/token",
                    json={"username": username, "password": "secret123"})
    return json.loads(r.get_data(as_text=True)).get("access_token")


def _get(client, url, token):
    r = client.get(url, headers={"Authorization": f"Bearer {token}"})
    return r.status_code, json.loads(r.get_data(as_text=True))


def _order(db, mol_env, **overrides):
    kwargs = dict(vehicle_id=mol_env["vehicle"].id,
                  maintenance_type_id=mol_env["mt"].id,
                  scheduled_date=date.today(), user=None)
    kwargs.update(overrides)
    order = MaintenanceOrderService().create(**kwargs)
    db.session.commit()
    return order


# ── Summary: physical status ────────────────────────────────────────────────

def test_summary_counts_physical_status(db, client, mol_env):
    # A vehicle may not carry two OPEN PMS orders at once (a real rule
    # in MaintenanceOrderService.create), so the second order needs its
    # own vehicle.
    second_vehicle = VehicleService().create(
        vehicle_type_id=mol_env["vt"].id, brand="Isuzu", model="Elf",
        year=2021, branch_id=mol_env["branch"].id,
        conduction_number="MOL-002")
    o1 = _order(db, mol_env)
    o2 = _order(db, mol_env, vehicle_id=second_vehicle.id)
    o2.status = "COMPLETED"
    db.session.commit()

    _status, body = _get(client, "/api/v1/maintenance-orders/summary",
                         _token(client))
    assert body["total"] == 2
    assert body["draft"] == 1
    assert body["completed"] == 1


def test_summary_never_invents_a_priority_field(db, client, mol_env):
    """The mockup this was built from showed a Priority tile and filter.
    No such column exists on the model, so none is invented here --
    this test exists to make removing that principle a visible,
    deliberate change rather than a silent one."""
    _order(db, mol_env)
    _status, body = _get(client, "/api/v1/maintenance-orders/summary",
                         _token(client))
    assert "priority" not in body
    assert "high_priority" not in body


# ── Summary: approval status, kept separate from physical status ───────────

def test_summary_counts_approval_status_separately_from_physical_status(
        db, client, mol_env):
    """An order can be APPROVED (approval status) while remaining DRAFT
    (physical status) -- these are two different facts about the SAME
    order, and a flat seven-tile list that mixes them cannot represent
    that without double-counting or losing one of them.

    With no ApprovalPath configured for the MO document type -- the
    state of a fresh install, and of this test's fixtures -- the engine
    auto-approves on submit rather than leaving the instance pending
    forever. Confirmed directly against the engine before writing this
    assertion: an unconfigured install must not silently strand every
    submission in limbo. That is itself the proof needed here: physical
    status (DRAFT, since work has not started) and approval status
    (APPROVED, since submission completed) move independently.
    """
    order = _order(db, mol_env)
    MaintenanceOrderService().submit(order.id, user=mol_env["user"])
    db.session.commit()

    _status, body = _get(client, "/api/v1/maintenance-orders/summary",
                         _token(client))
    assert body["approved"] == 1
    # Physical status is unaffected by submitting -- the order stays
    # DRAFT until work actually starts, which is a separate action.
    assert body["draft"] == 1


def test_an_order_with_no_approval_instance_counts_toward_neither_approval_bucket(
        db, client, mol_env):
    """Most orders never need approval at all (requires_approval=False
    is the default). They must not silently inflate 'rejected' or
    'pending_approval' -- those buckets describe orders that ENTERED the
    workflow, not every order that exists."""
    _order(db, mol_env)
    _status, body = _get(client, "/api/v1/maintenance-orders/summary",
                         _token(client))
    assert body["pending_approval"] == 0
    assert body["approved"] == 0
    assert body["rejected"] == 0


def test_summary_respects_org_scope(db, client, mol_env):
    from app.modules.user_management.org_scope_service import (
        UserOrgScopeService)
    from app.modules.master_data.org.service import BranchService

    other_branch = BranchService().create(code="BR-MOL2", name="Other")
    from app.modules.master_data.vehicle.service import VehicleService
    other_vehicle = VehicleService().create(
        vehicle_type_id=mol_env["vt"].id, brand="Isuzu", model="Elf",
        year=2020, branch_id=other_branch.id, conduction_number="MOL-OTH")
    _order(db, mol_env)
    _order(db, mol_env, vehicle_id=other_vehicle.id)

    UserOrgScopeService().assign(mol_env["user"].id, scope_type="BRANCH",
                                 branch_id=mol_env["branch"].id)
    db.session.commit()

    _status, body = _get(client, "/api/v1/maintenance-orders/summary",
                         _token(client))
    assert body["total"] == 1


def test_summary_requires_view_permission(db, client, mol_env):
    from app.modules.user_management.models import Role, User
    role = Role(name="No MO Access")
    user = User(username="nomol", email="nm@e.com",
                password_hash=hash_password("secret123"), is_active=True)
    user.roles = [role]
    db.session.add_all([role, user])
    db.session.commit()
    status, _ = _get(client, "/api/v1/maintenance-orders/summary",
                     _token(client, "nomol"))
    assert status == 403


# ── List row: PR / Invoice ──────────────────────────────────────────────────

def test_list_row_carries_the_purchase_request_document_number(db, client,
                                                                mol_env):
    _ensure_doc_type("PR")
    from app.modules.transactions.purchase_request.models import (
        PurchaseRequest)
    order = _order(db, mol_env)
    pr = PurchaseRequest(document_number="PR-2026-000111",
                         requested_by=mol_env["user"].id, status="DRAFT")
    db.session.add(pr)
    db.session.flush()
    order.purchase_request_id = pr.id
    db.session.commit()

    _status, body = _get(client, "/api/v1/maintenance-orders", _token(client))
    row = next(r for r in body["items"] if r["id"] == order.id)
    assert row["pr_document_number"] == "PR-2026-000111"


def test_list_row_has_no_pr_number_when_none_generated(db, client, mol_env):
    """Absent, not a placeholder string. Most orders never generate a
    PR at all, and an em dash on the client is a presentation choice --
    the API should not decide what "no PR" looks like."""
    order = _order(db, mol_env)
    _status, body = _get(client, "/api/v1/maintenance-orders", _token(client))
    row = next(r for r in body["items"] if r["id"] == order.id)
    assert row["pr_document_number"] is None


def test_list_row_carries_the_latest_invoice_document_number(db, client,
                                                             mol_env):
    from app.modules.master_data.vendor.service import VendorService
    from app.modules.transactions.maintenance_invoice.service import (
        MaintenanceInvoiceService)

    _ensure_doc_type("INV")
    vendor = VendorService().create(code="VEND-MOL", name="MOL Motors",
                                    vendor_type="SERVICES",
                                    email="v@e.com", contact_person="J",
                                    phone="0917")
    order = _order(db, mol_env)
    MaintenanceInvoiceService().create(
        maintenance_order_id=order.id, vendor_id=vendor.id,
        invoice_number="SI-1", invoice_date=date.today(), user=None)
    db.session.commit()

    _status, body = _get(client, "/api/v1/maintenance-orders", _token(client))
    row = next(r for r in body["items"] if r["id"] == order.id)
    assert row["invoice_document_number"] is not None


# ── List row: Docs (attachment count) ───────────────────────────────────────

def test_list_row_carries_the_attachment_count(db, client, mol_env):
    from app.core.models.attachment import Attachment

    order = _order(db, mol_env)
    db.session.add_all([
        Attachment(reference_table="maintenance_orders",
                  reference_id=order.id, filename="a.pdf",
                  original_filename="a.pdf", file_size=10),
        Attachment(reference_table="maintenance_orders",
                  reference_id=order.id, filename="b.pdf",
                  original_filename="b.pdf", file_size=10),
    ])
    db.session.commit()

    _status, body = _get(client, "/api/v1/maintenance-orders", _token(client))
    row = next(r for r in body["items"] if r["id"] == order.id)
    assert row["attachment_count"] == 2


def test_attachment_count_is_zero_not_absent_when_there_are_none(db, client,
                                                                 mol_env):
    """Zero is the honest count. A missing key would make the client
    guess whether zero or "not yet loaded" is meant."""
    order = _order(db, mol_env)
    _status, body = _get(client, "/api/v1/maintenance-orders", _token(client))
    row = next(r for r in body["items"] if r["id"] == order.id)
    assert row["attachment_count"] == 0


# ── List row: Requested By ──────────────────────────────────────────────────

def test_list_row_carries_the_requesters_name(db, client, mol_env):
    order = _order(db, mol_env, user=mol_env["user"])
    _status, body = _get(client, "/api/v1/maintenance-orders", _token(client))
    row = next(r for r in body["items"] if r["id"] == order.id)
    assert row["requested_by"] is not None
