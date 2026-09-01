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
    status, body = _post(
        client, f"/api/v1/purchase-requests/{pr_body['id']}/submit",
        _token(client))
    # NOT `assert status in (200, 409)`. That assertion is what let
    # BaseTransactionService.submit() got an unexpected keyword
    # argument 'remarks' ship silently for months: the dispatcher's
    # generic except Exception -> _conflict(str(exc)) turns a genuine
    # TypeError into the exact same 409 shape as a real business-rule
    # refusal, and a test that accepts either status cannot tell a
    # crash from a legitimate "wrong state" answer. A fresh DRAFT PR
    # submitting for the first time must succeed outright.
    assert status == 200, body


def test_approval_actions_are_not_404_placeholders(db, client, pr_env):
    _status, pr_body = _post(client, "/api/v1/purchase-requests",
                             _token(client), _pr_payload(pr_env))
    for action in ("submit", "approve", "reject", "return", "cancel",
                   "mark-ordered", "mark-received"):
        status, _ = _post(
            client, f"/api/v1/purchase-requests/{pr_body['id']}/{action}",
            _token(client))
        assert status != 404, f"/{action} is not routed"


def test_mark_received_transitions_an_ordered_pr(db, client, pr_env):
    """Reported live: the React button (shown only when status ==
    ORDERED, matching this test's setup) called this exact URL and got
    a 404 -- the mark_received() service method has existed all along,
    the route to reach it never did. Caught here rather than only by
    the routing check above so a regression shows a real status
    mismatch, not just 'some non-404 code came back'.

    Also mutation-relevant: mark_received(self, pr_id) takes no user or
    remarks, the same shape as mark_ordered -- routing it through the
    dispatcher's generic branch, which forwards both, would repeat the
    exact bug just fixed for submit()."""
    _status, pr_body = _post(client, "/api/v1/purchase-requests",
                             _token(client), _pr_payload(pr_env))
    pid = pr_body["id"]
    status, body = _post(client, f"/api/v1/purchase-requests/{pid}/mark-ordered",
                         _token(client))
    assert status == 200, body
    assert body["status"] == "ORDERED"

    status, body = _post(client, f"/api/v1/purchase-requests/{pid}/mark-received",
                         _token(client))
    assert status == 200, body
    assert body["status"] == "RECEIVED"


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


def test_resubmit_is_routed(db, client, pr_env):
    _status, pr_body = _post(client, "/api/v1/purchase-requests",
                             _token(client), _pr_payload(pr_env))
    status, _ = _post(
        client, f"/api/v1/purchase-requests/{pr_body['id']}/resubmit",
        _token(client))
    assert status != 404


# ── Detail: approval chain + action-visibility decisions ────────────────────

def test_detail_carries_the_approval_chain_shape(db, client, pr_env):
    """Same shape Maintenance Orders already return, so ApprovalWorkflow
    (React) renders both without a second component."""
    _status, pr_body = _post(client, "/api/v1/purchase-requests",
                             _token(client), _pr_payload(pr_env))
    _status, detail = _get(
        client, f"/api/v1/purchase-requests/{pr_body['id']}", _token(client))
    for key in ("approval_chain", "approval_instance_status",
               "can_act", "has_approval_instance", "is_requester"):
        assert key in detail, f"{key} missing"
    assert detail["approval_chain"] == []
    assert detail["has_approval_instance"] is False


def test_is_requester_reflects_who_created_it(db, client, pr_env):
    _status, pr_body = _post(client, "/api/v1/purchase-requests",
                             _token(client), _pr_payload(pr_env))
    _status, detail = _get(
        client, f"/api/v1/purchase-requests/{pr_body['id']}", _token(client))
    assert detail["is_requester"] is True

    _status, detail2 = _get(
        client, f"/api/v1/purchase-requests/{pr_body['id']}",
        _token(client, "prviewer"))
    assert detail2["is_requester"] is False


# ── Creating a PR linked to an MO (the "New PR" entry point) ────────────────

def test_create_can_link_to_an_mo(db, client, pr_env):
    """The "New Purchase Request" entry point from the MO screen: a
    MANUALLY created PR that still ends up linked, the same way
    generate_purchase_request links one automatically. Without this,
    "New PR under MO" would create a PR with no way back to the order
    that prompted it."""
    order = MaintenanceOrderService().create(
        vehicle_id=pr_env["vehicle"].id, maintenance_type_id=pr_env["mt"].id,
        scheduled_date=date.today(), user=None)
    db.session.commit()

    status, body = _post(client, "/api/v1/purchase-requests", _token(client),
                         _pr_payload(pr_env, maintenance_order_id=order.id))
    assert status == 201, body
    assert body["linked_mo_id"] == order.id

    from app.modules.transactions.maintenance_order.models import (
        MaintenanceOrder)
    fresh = MaintenanceOrder.query.filter_by(id=order.id).first()
    assert fresh.purchase_request_id == body["id"]


def test_create_refuses_to_link_an_mo_that_already_has_a_pr(db, client,
                                                            pr_env):
    """Same guard as generate_purchase_request: a second PR would
    duplicate the procurement and double-order the same parts."""
    order = MaintenanceOrderService().create(
        vehicle_id=pr_env["vehicle"].id, maintenance_type_id=pr_env["mt"].id,
        scheduled_date=date.today(), user=None)
    db.session.commit()
    _status, first = _post(client, "/api/v1/purchase-requests",
                           _token(client),
                           _pr_payload(pr_env, maintenance_order_id=order.id))
    assert _status == 201, first

    status, body = _post(client, "/api/v1/purchase-requests", _token(client),
                         _pr_payload(pr_env, maintenance_order_id=order.id))
    assert status == 409, body


def test_create_with_an_unknown_mo_id_is_a_field_error(db, client, pr_env):
    status, body = _post(client, "/api/v1/purchase-requests", _token(client),
                         _pr_payload(pr_env, maintenance_order_id=999999))
    assert status == 400, body


def test_create_without_an_mo_id_is_unlinked_as_before(db, client, pr_env):
    """The default path -- a standalone PR, matching Flask's own form,
    which has no MO field at all. Must not break."""
    status, body = _post(client, "/api/v1/purchase-requests", _token(client),
                         _pr_payload(pr_env))
    assert status == 201, body
    assert body["linked_mo_id"] is None


# ── Department reference: branch context and filtering ──────────────────────

def test_departments_carry_the_branch_name_to_disambiguate_duplicates(
        db, client, pr_env):
    """Two branches can each have their own department named the same
    thing -- "Fleet Operations" at Manila Hub and at Cebu Hub are
    different rows. A flat dropdown showing "Fleet Operations" twice
    with no branch context is unusable at any real scale."""
    from app.modules.master_data.org.service import BranchService, DepartmentService

    other_branch = BranchService().create(code="BR-DEPT2", name="Cebu Hub")
    DepartmentService().create(code="DEPT-DUP", name="Fleet Operations",
                              branch_id=other_branch.id)
    db.session.commit()

    r = client.get("/api/v1/reference/departments",
                   headers={"Authorization": f"Bearer {_token(client)}"})
    body = json.loads(r.get_data(as_text=True))
    names = {d["branch_name"] for d in body["items"]}
    assert pr_env["branch"].name in names
    assert "Cebu Hub" in names


def test_departments_can_be_filtered_to_one_branch(db, client, pr_env):
    """The hierarchy: pick a branch first, and the department list
    narrows to just that branch's departments -- exactly what removes
    the duplicate-looking entries rather than merely labelling them."""
    from app.modules.master_data.org.service import BranchService, DepartmentService

    other_branch = BranchService().create(code="BR-DEPT3", name="Davao Hub")
    DepartmentService().create(code="DEPT-DAVAO", name="Fleet Operations",
                              branch_id=other_branch.id)
    db.session.commit()

    r = client.get(
        f"/api/v1/reference/departments?branch_id={pr_env['branch'].id}",
        headers={"Authorization": f"Bearer {_token(client)}"})
    body = json.loads(r.get_data(as_text=True))
    assert all(d["branch_id"] == pr_env["branch"].id for d in body["items"])
    assert len(body["items"]) == 1


def test_full_link_roundtrip_survives_a_shared_session(db, client, pr_env):
    """Reproduces exactly what a user does: create an MO, raise a PR
    from its 'New Purchase Request' button, then re-read the MO.

    Found by actually running this sequence rather than trusting the
    unit-level tests above: pr_id updated correctly after linking, but
    pr_document_number stayed None. order.purchase_request_id (the raw
    FK) and order.purchase_request (the RELATIONSHIP) do not
    necessarily go stale together -- a caller that read the order
    BEFORE the link was created, in the same session, can hold a
    lazy-loaded relationship cached as None from that earlier read,
    and setting the FK plus committing elsewhere does not retroactively
    invalidate a cache that already existed on a different reference to
    the same row.

    Fixed with db.session.expire(order, ["purchase_request"]) right
    after the write, forcing the next access to re-query rather than
    trust a cache that predates it.
    """
    order = MaintenanceOrderService().create(
        vehicle_id=pr_env["vehicle"].id, maintenance_type_id=pr_env["mt"].id,
        scheduled_date=date.today(), user=None)
    db.session.commit()
    t = _token(client)

    # Read the order BEFORE linking -- this is what makes the bug
    # reproducible: it is what populates the stale relationship cache
    # that the fix has to invalidate.
    _status, before = _get(client, f"/api/v1/maintenance-orders/{order.id}", t)
    assert before["pr_id"] is None

    _status, pr = _post(client, "/api/v1/purchase-requests", t,
                        _pr_payload(pr_env, maintenance_order_id=order.id))
    assert pr["linked_mo_id"] == order.id

    _status, after = _get(client, f"/api/v1/maintenance-orders/{order.id}", t)
    assert after["pr_id"] == pr["id"]
    assert after["pr_document_number"] == pr["document_number"]

    _status, mo_list = _get(client, "/api/v1/maintenance-orders", t)
    row = next(r for r in mo_list["items"] if r["id"] == order.id)
    assert row["pr_document_number"] == pr["document_number"]

    _status, pr_detail = _get(
        client, f"/api/v1/purchase-requests/{pr['id']}", t)
    assert pr_detail["linked_mo_document_number"] == order.document_number


def test_pr_detail_carries_the_configured_company_letterhead(db, client,
                                                              pr_env):
    """Same fix as MO: print headers were hardcoded rather than reading
    System Administration's Company Profile, which every one of Flask's
    print templates uses."""
    from app.modules.system_admin.services.company_service import (
        CompanyProfileService)

    CompanyProfileService().save(
        company_name="Excellence Poultry & Livestock Specialist Inc.",
        address_line1="123 Governor's Drive", city="Carmona, Cavite")
    db.session.commit()

    _status, pr = _post(client, "/api/v1/purchase-requests", _token(client),
                        _pr_payload(pr_env))
    _status, detail = _get(
        client, f"/api/v1/purchase-requests/{pr['id']}", _token(client))
    assert detail["company"]["company_name"] == (
        "Excellence Poultry & Livestock Specialist Inc.")
    assert detail["company"]["city"] == "Carmona, Cavite"


def test_pr_detail_company_is_empty_when_unconfigured(db, client, pr_env):
    _status, pr = _post(client, "/api/v1/purchase-requests", _token(client),
                        _pr_payload(pr_env))
    _status, detail = _get(
        client, f"/api/v1/purchase-requests/{pr['id']}", _token(client))
    assert detail["company"] == {}
