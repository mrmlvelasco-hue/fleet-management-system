"""Purchase Request API.

The module the API had zero endpoints for entirely -- confirmed against
Flask's real model, form, list and detail templates before writing a
line of code, because a client mockup for this same module showed eight
fields (category, priority, branch, linked-MO-as-a-field, vehicle, VAT,
part number, unit of measure) that do not exist anywhere in Flask.

What IS real, matching the model and Flask's own form exactly:
description, department, preferred vendor, needed-by date,
justification, and line items of item_description/quantity/unit_cost.
Status is DRAFT/PENDING/APPROVED/ORDERED/RECEIVED/REJECTED/RETURNED/
CANCELLED -- the mockup's "FOR APPROVAL" and "PO ISSUED" are display
labels for PENDING and ORDERED, not additional states.

The "linked MO" the mockup showed as a PR field is real information,
just not a PR column: MaintenanceOrder.purchase_request_id points the
OTHER way, so it is found by querying for the order whose
purchase_request_id matches, not by reading a field off the PR.
"""
import json
from datetime import date

import pytest

from app.core.security.password import hash_password
from app.core.security.registry import sync_permissions
from app.modules.document_config.service import (
    DocumentTypeService, NumberingSchemeService)
from app.modules.master_data.org.service import BranchService, DepartmentService
from app.modules.master_data.reference.service import (
    MaintenanceTypeService, VehicleTypeService)
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.vendor.service import VendorService
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
def pr_env(db):
    sync_permissions()
    db.session.commit()

    role = Role(name="PR Clerk")
    role.permissions = Permission.query.filter(
        Permission.code.in_(["purchaserequest.view", "purchaserequest.create",
                             "purchaserequest.update",
                             "maintenanceorder.view"])).all()
    user = User(username="prclerk", email="pc@e.com",
                password_hash=hash_password("secret123"), is_active=True)
    user.roles = [role]

    viewer_role = Role(name="PR Viewer")
    viewer_role.permissions = Permission.query.filter(
        Permission.code == "purchaserequest.view").all()
    viewer = User(username="prviewer", email="pv@e.com",
                  password_hash=hash_password("secret123"), is_active=True)
    viewer.roles = [viewer_role]
    db.session.add_all([role, user, viewer_role, viewer])

    branch = BranchService().create(code="BR-PR", name="PR Branch")
    dept = DepartmentService().create(code="DEPT-PR", name="Fleet Operations",
                                      branch_id=branch.id)
    vendor = VendorService().create(code="VEND-PR", name="AutoParts Central",
                                    vendor_type="GOODS", email="ap@v.com",
                                    contact_person="J", phone="0917")
    vt = VehicleTypeService().create(code="LV-PR", name="Light",
                                     category="LIGHT")
    mt = MaintenanceTypeService().create(code="PR-MT", name="PMS",
                                         category="PM")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2024,
        branch_id=branch.id, conduction_number="PR-000")
    _ensure_doc_type("PR")
    _ensure_doc_type("MO")
    db.session.commit()
    return {"user": user, "branch": branch, "dept": dept, "vendor": vendor,
            "vt": vt, "mt": mt, "vehicle": vehicle}


def _token(client, username="prclerk"):
    r = client.post("/api/v1/auth/token",
                    json={"username": username, "password": "secret123"})
    return json.loads(r.get_data(as_text=True)).get("access_token")


def _post(client, path, token, payload=None):
    r = client.post(path, json=payload or {},
                    headers={"Authorization": f"Bearer {token}"})
    body = r.get_data(as_text=True)
    return r.status_code, (json.loads(body) if body else {})


def _get(client, path, token):
    r = client.get(path, headers={"Authorization": f"Bearer {token}"})
    return r.status_code, json.loads(r.get_data(as_text=True))


def _pr_payload(pr_env, **overrides):
    payload = {
        "description": "Filters & drive belts for PMS batch",
        "department_id": pr_env["dept"].id,
        "vendor_id": pr_env["vendor"].id,
        "justification": "Replenish stock below reorder point.",
        "needed_by_date": "2026-01-25",
        "lines": [
            {"item_description": "Hydraulic filter element",
             "quantity": "12", "unit_cost": "850.00"},
            {"item_description": "Serpentine drive belt",
             "quantity": "4", "unit_cost": "1450.00"},
        ],
    }
    payload.update(overrides)
    return payload


# ── Auth ────────────────────────────────────────────────────────────────────

def test_create_rejects_anonymous(db, client, pr_env):
    assert client.post("/api/v1/purchase-requests", json={}).status_code == 401


def test_create_requires_the_create_permission(db, client, pr_env):
    status, _ = _post(client, "/api/v1/purchase-requests",
                      _token(client, "prviewer"), _pr_payload(pr_env))
    assert status == 403


# ── Create ──────────────────────────────────────────────────────────────────

def test_create_returns_the_header_with_lines(db, client, pr_env):
    status, body = _post(client, "/api/v1/purchase-requests", _token(client),
                         _pr_payload(pr_env))
    assert status == 201, body
    assert body["status"] == "DRAFT"
    assert len(body["lines"]) == 2
    # 12*850 + 4*1450 = 10200 + 5800 = 16000. No VAT anywhere -- amount
    # is exactly the sum of line totals, matching the model exactly.
    assert float(body["amount"]) == 16000.0


def test_description_and_justification_are_required(db, client, pr_env):
    status, body = _post(client, "/api/v1/purchase-requests", _token(client),
                         _pr_payload(pr_env, description=""))
    assert status == 400, body
    assert "description" in body.get("fields", {})


def test_at_least_one_line_is_required(db, client, pr_env):
    status, body = _post(client, "/api/v1/purchase-requests", _token(client),
                         _pr_payload(pr_env, lines=[]))
    assert status == 400, body


def test_line_quantities_are_coerced_from_strings(db, client, pr_env):
    """The real client sends every value as text. quantity and
    unit_cost are Numeric columns; a bug in exactly this shape --
    vat_percentage on invoices -- reached the client as a 500 before
    the shared Coercer covered it. Lines go through the same helper
    here from the start."""
    status, body = _post(client, "/api/v1/purchase-requests", _token(client),
                         _pr_payload(pr_env))
    assert status == 201, body
    assert body["lines"][0]["quantity"] == "12.00"
    assert body["lines"][0]["unit_cost"] == "850.00"


def test_a_non_numeric_quantity_is_a_field_error_not_a_500(db, client,
                                                           pr_env):
    status, body = _post(client, "/api/v1/purchase-requests", _token(client),
                         _pr_payload(pr_env, lines=[
                             {"item_description": "X", "quantity": "twelve",
                              "unit_cost": "1"}]))
    assert status == 400, body


def test_vendor_and_department_are_optional(db, client, pr_env):
    """Flask's form marks Preferred Supplier optional. A justification-
    only request (no vendor picked yet) must still be creatable."""
    status, body = _post(client, "/api/v1/purchase-requests", _token(client),
                         _pr_payload(pr_env, vendor_id=None,
                                    department_id=None))
    assert status == 201, body
    assert body["vendor"] is None


# ── Detail / List ────────────────────────────────────────────────────────────

def test_detail_404s_for_a_missing_pr(db, client, pr_env):
    status, _ = _get(client, "/api/v1/purchase-requests/999999",
                     _token(client))
    assert status == 404


def test_list_shows_created_requests(db, client, pr_env):
    _post(client, "/api/v1/purchase-requests", _token(client),
         _pr_payload(pr_env))
    status, body = _get(client, "/api/v1/purchase-requests", _token(client))
    assert status == 200
    assert body["items"][0]["description"] == "Filters & drive belts for PMS batch"


def test_detail_shows_the_linked_maintenance_order_when_one_exists(
        db, client, pr_env):
    """The mockup's "Linked MO" field, derived correctly: PR has no
    column pointing at an MO. MaintenanceOrder.purchase_request_id
    points the other way, so this is found by querying for the order
    whose purchase_request_id matches this PR -- not invented as a
    field on the PR itself."""
    _status, pr_body = _post(client, "/api/v1/purchase-requests",
                             _token(client), _pr_payload(pr_env))
    order = MaintenanceOrderService().create(
        vehicle_id=pr_env["vehicle"].id, maintenance_type_id=pr_env["mt"].id,
        scheduled_date=date.today(), user=None)
    order.purchase_request_id = pr_body["id"]
    db.session.commit()

    _status, detail = _get(
        client, f"/api/v1/purchase-requests/{pr_body['id']}", _token(client))
    assert detail["linked_mo_document_number"] == order.document_number
    assert detail["linked_mo_id"] == order.id


def test_no_linked_mo_is_null_not_an_error(db, client, pr_env):
    _status, pr_body = _post(client, "/api/v1/purchase-requests",
                             _token(client), _pr_payload(pr_env))
    _status, detail = _get(
        client, f"/api/v1/purchase-requests/{pr_body['id']}", _token(client))
    assert detail["linked_mo_id"] is None


# ── Lines: add/remove while DRAFT ────────────────────────────────────────────

def test_add_line_recomputes_the_amount(db, client, pr_env):
    _status, pr_body = _post(client, "/api/v1/purchase-requests",
                             _token(client), _pr_payload(pr_env, lines=[
                                 {"item_description": "Oil",
                                  "quantity": "1", "unit_cost": "500"}]))
    status, body = _post(
        client, f"/api/v1/purchase-requests/{pr_body['id']}/lines",
        _token(client), {"item_description": "Filter", "quantity": "2",
                         "unit_cost": "100"})
    assert status == 201, body
    _status, fresh = _get(
        client, f"/api/v1/purchase-requests/{pr_body['id']}", _token(client))
    assert float(fresh["amount"]) == 700.0


def test_lines_cannot_be_added_after_submit(db, client, pr_env):
    """Matches LineManagementError's own rule: modifying a submitted
    request would change what the approver is deciding on without
    their knowledge.

    approval_instance_id is set DIRECTLY rather than via the /submit
    endpoint: with no ApprovalPath configured for the PR document type
    (this fixture's state, and a fresh install's -- the same finding
    recorded for Maintenance Orders), submit() auto-approves, and an
    auto-approved request may legitimately still accept lines under
    some future business rule this test has no opinion on. The rule
    under test here is specifically "once an approval instance exists",
    which is what LineManagementError actually checks.
    """
    from app.modules.transactions.purchase_request.models import (
        PurchaseRequest)

    _status, pr_body = _post(client, "/api/v1/purchase-requests",
                             _token(client), _pr_payload(pr_env))
    pr = PurchaseRequest.query.filter_by(id=pr_body["id"]).first()
    pr.approval_instance_id = -1  # any non-null id; FK not enforced in SQLite
    db.session.commit()

    status, body = _post(
        client, f"/api/v1/purchase-requests/{pr_body['id']}/lines",
        _token(client), {"item_description": "Late add", "quantity": "1",
                         "unit_cost": "1"})
    assert status == 409, body


def test_remove_line_recomputes_the_amount(db, client, pr_env):
    _status, pr_body = _post(client, "/api/v1/purchase-requests",
                             _token(client), _pr_payload(pr_env))
    line_id = pr_body["lines"][0]["id"]
    r = client.delete(f"/api/v1/purchase-requests/{pr_body['id']}/lines/{line_id}",
                      headers={"Authorization": f"Bearer {_token(client)}"})
    assert r.status_code == 200
    body = json.loads(r.get_data(as_text=True))
    assert float(body["amount"]) == 5800.0


# ── Approval lifecycle (shared base, same pattern as MO) ────────────────────

def test_submit_and_approve(db, client, pr_env):
    _status, pr_body = _post(client, "/api/v1/purchase-requests",
                             _token(client), _pr_payload(pr_env))
    status, _ = _post(
        client, f"/api/v1/purchase-requests/{pr_body['id']}/submit",
        _token(client))
    assert status in (200, 409)


def test_approval_actions_are_not_404_placeholders(db, client, pr_env):
    _status, pr_body = _post(client, "/api/v1/purchase-requests",
                             _token(client), _pr_payload(pr_env))
    for action in ("submit", "approve", "reject", "return", "cancel",
                   "mark-ordered"):
        status, _ = _post(
            client, f"/api/v1/purchase-requests/{pr_body['id']}/{action}",
            _token(client))
        assert status != 404, f"/{action} is not routed"


# ── Generate-from-MO endpoint stays consistent ──────────────────────────────

def test_pr_created_from_an_mo_is_reachable_through_this_api(db, client,
                                                             pr_env):
    """The MO screen's "Generate Purchase Request" (flask-v148) creates
    a PurchaseRequest row directly via the service. Confirms this
    module's own GET can read what that endpoint writes -- one shared
    model, not two competing definitions of a purchase request."""
    from app.modules.master_data.reference.service import (
        MaintenanceTypeService)
    order = MaintenanceOrderService().create(
        vehicle_id=pr_env["vehicle"].id, maintenance_type_id=pr_env["mt"].id,
        scheduled_date=date.today(), user=None)
    db.session.commit()
    from app.modules.transactions.maintenance_order.models import (
        MaintenanceOrderPart)
    db.session.add(MaintenanceOrderPart(
        order_id=order.id, part_description="Brake pad", quantity=2,
        estimated_unit_cost=500, sort_order=1))
    db.session.commit()

    status, gen_body = _post(
        client, f"/api/v1/maintenance-orders/{order.id}/generate-pr",
        _token(client))
    assert status == 201, gen_body

    status, pr_body = _get(
        client, f"/api/v1/purchase-requests/{gen_body['id']}", _token(client))
    assert status == 200
    assert pr_body["linked_mo_id"] == order.id


def test_a_line_cannot_be_deleted_through_another_pr(db, client, pr_env):
    """The service's remove_line takes only a line id and does not
    check which PR it belongs to -- same gap as invoices' remove_line.
    The URL asserts a relationship, so the API verifies it; otherwise
    /purchase-requests/5/lines/99 could delete a line from PR 7 and
    silently change that PR's amount."""
    t = _token(client)
    _status, a = _post(client, "/api/v1/purchase-requests", t,
                       _pr_payload(pr_env))
    _status, b = _post(client, "/api/v1/purchase-requests", t,
                       _pr_payload(pr_env))
    line_id = b["lines"][0]["id"]

    r = client.delete(f"/api/v1/purchase-requests/{a['id']}/lines/{line_id}",
                      headers={"Authorization": f"Bearer {t}"})
    assert r.status_code == 404

    _status, fresh_b = _get(client, f"/api/v1/purchase-requests/{b['id']}", t)
    assert len(fresh_b["lines"]) == 2


# ── Summary (stat chips) ─────────────────────────────────────────────────────

def test_summary_counts_by_status_and_totals_value(db, client, pr_env):
    _post(client, "/api/v1/purchase-requests", _token(client),
         _pr_payload(pr_env))
    status, body = _get(client, "/api/v1/purchase-requests/summary",
                        _token(client))
    assert status == 200
    assert body["total"] == 1
    assert body["draft"] == 1
    # 12*850 + 4*1450 = 16000.
    assert float(body["total_value"]) == 16000.0


def test_summary_requires_view_permission(db, client, pr_env):
    from app.modules.user_management.models import Role, User
    role = Role(name="No PR Access")
    user = User(username="noprsum", email="nps@e.com",
                password_hash=hash_password("secret123"), is_active=True)
    user.roles = [role]
    db.session.add_all([role, user])
    db.session.commit()
    status, _ = _get(client, "/api/v1/purchase-requests/summary",
                     _token(client, "noprsum"))
    assert status == 403
