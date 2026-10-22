"""Mobile Approval Inbox read model: GET /api/v1/approvals/inbox and
GET /api/v1/approvals/<reference_table>/<id>/card.

Why this exists (source review, 2026-10-22): the mobile Approval Inbox
mockup shows, per card, the vehicle, branch, requester, amount, current
approval level and status, and offers All / Pending / Approved /
Rejected filters. The existing endpoints could not supply that:

  * /worklist/pending-approvals and /worklist/inbox return only the
    reference, document type, level and date for PENDING tasks;
  * nothing returned the documents an approver has already approved or
    rejected.

This is a READ model over the existing engine tables -- ApprovalTask,
ApprovalInstance, ApprovalAction -- not a second workflow. Who may see a
PENDING item is still ApprovalTaskService.list_for_user(); decided items
are only ever the caller's OWN recorded decisions. Approving still goes
through each module's existing /approve, /reject, /return endpoint.
"""
import json
from datetime import date

import pytest

from app.core.security.password import hash_password
from app.core.security.registry import sync_permissions
from app.extensions import db as _db
from app.modules.approval_config.service import (
    ApprovalMatrixService, ApprovalPathService)
from app.modules.document_config.service import (
    DocumentTypeService, NumberingSchemeService)
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.reference.service import (
    MaintenanceTypeService, VehicleTypeService)
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.transactions.maintenance_order.service import (
    MaintenanceOrderService)
from app.modules.user_management.models import Permission, Role, User


def _user(username, roles, full_name=None):
    u = User(username=username, email=f"{username}@e.com",
             password_hash=hash_password("secret123"), is_active=True)
    if full_name:
        parts = full_name.split(" ", 1)
        if hasattr(u, "first_name"):
            u.first_name = parts[0]
            u.last_name = parts[1] if len(parts) > 1 else ""
    u.roles = roles
    _db.session.add(u)
    return u


@pytest.fixture()
def env(db):
    sync_permissions()
    db.session.commit()
    view = Permission.query.filter(
        Permission.code.in_(["maintenanceorder.view"])).all()

    r1 = Role(name="Fleet Officer L1")
    r2 = Role(name="Fleet Manager L2")
    r1.permissions = list(view)
    r2.permissions = list(view)
    _db.session.add_all([r1, r2])
    l1 = _user("inbox_l1", [r1])
    l2 = _user("inbox_l2", [r2])
    outsider = _user("inbox_out", [Role(name="Nobody Inbox")])
    requester = _user("inbox_req", [r1])
    _db.session.commit()

    branch = BranchService().create(code="BR-INB", name="Laguna Branch")
    vt = VehicleTypeService().create(code="LV-INB", name="Light",
                                     category="LIGHT")
    mt = MaintenanceTypeService().create(code="INB-PMS", name="PMS",
                                         category="PM")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Nissan", model="Navara", year=2024,
        branch_id=branch.id, conduction_number="INB-000",
        plate_number="ABC-1234")

    dt = DocumentTypeService().create(code="MO", name="Maintenance Order",
                                      requires_approval=True,
                                      auto_numbering=True)
    NumberingSchemeService().create(document_type_id=dt.id, prefix="MO",
                                    include_year=True, digit_count=6,
                                    reset_policy="YEARLY")
    path = ApprovalPathService().create(name="Inbox Two Level", levels=[
        {"level_number": 1, "approver_type": "ROLE", "role_id": r1.id},
        {"level_number": 2, "approver_type": "ROLE", "role_id": r2.id},
    ])
    ApprovalMatrixService().create(dt.id, path.id, min_amount=None,
                                   max_amount=None)
    _db.session.commit()
    return {"l1": l1, "l2": l2, "outsider": outsider,
            "requester": requester, "vehicle": vehicle, "mt": mt,
            "branch": branch}


def _submitted_order(env, cost=12500):
    svc = MaintenanceOrderService()
    o = svc.create(vehicle_id=env["vehicle"].id,
                   maintenance_type_id=env["mt"].id,
                   scheduled_date=date.today(), user=env["requester"],
                   description="6 months service", estimated_cost=cost)
    svc.submit(o.id, user=env["requester"])
    _db.session.commit()
    return o


def _token(client, username):
    r = client.post("/api/v1/auth/token",
                    json={"username": username, "password": "secret123"})
    return json.loads(r.get_data(as_text=True))["access_token"]


def _get(client, url, username):
    r = client.get(url, headers={
        "Authorization": f"Bearer {_token(client, username)}"})
    return r.status_code, json.loads(r.get_data(as_text=True))


# ---------------------------------------------------------------- inbox

def test_inbox_requires_a_token(db, client, env):
    assert client.get("/api/v1/approvals/inbox").status_code == 401


def test_pending_card_carries_what_the_mockup_shows(db, client, env):
    o = _submitted_order(env)

    status, body = _get(client, "/api/v1/approvals/inbox", "inbox_l1")

    assert status == 200
    assert len(body["items"]) == 1
    card = body["items"][0]
    assert card["reference_table"] == "maintenance_orders"
    assert card["reference_id"] == o.id
    assert card["document_label"] == "Maintenance Order"
    assert card["document_number"] == o.document_number
    assert card["plate_number"] == "ABC-1234"
    assert card["vehicle_label"] == "Nissan Navara"
    assert card["branch"] == "Laguna Branch"
    assert card["requester"]  # a name, never blank for a submitted doc
    assert card["amount"] == 12500.0
    assert card["current_level"] == 1
    assert card["current_level_label"] == "Fleet Officer L1"
    assert card["status"] == "PENDING"
    assert card["my_action"] is None
    assert card["submitted_at"]
    assert card["pending_since"]
    assert card["url"] == f"/maintenance-orders/{o.id}"
    assert body["counts"]["pending"] == 1


def test_inbox_shows_nothing_to_someone_not_on_the_path(db, client, env):
    _submitted_order(env)
    status, body = _get(client, "/api/v1/approvals/inbox", "inbox_out")
    assert status == 200
    assert body["items"] == []
    assert body["counts"] == {"all": 0, "pending": 0, "approved": 0,
                              "rejected": 0}


def test_level_two_sees_it_only_after_level_one_approves(db, client, env):
    o = _submitted_order(env)
    _, before = _get(client, "/api/v1/approvals/inbox", "inbox_l2")
    assert before["items"] == []

    MaintenanceOrderService().approve(o.id, user=env["l1"])
    _db.session.commit()

    _, after = _get(client, "/api/v1/approvals/inbox?filter=pending",
                    "inbox_l2")
    assert [c["reference_id"] for c in after["items"]] == [o.id]
    assert after["items"][0]["current_level"] == 2


def test_approved_filter_lists_my_own_approvals(db, client, env):
    o = _submitted_order(env)
    MaintenanceOrderService().approve(o.id, user=env["l1"],
                                      remarks="Looks fine")
    _db.session.commit()

    _, pending = _get(client, "/api/v1/approvals/inbox?filter=pending",
                      "inbox_l1")
    _, approved = _get(client, "/api/v1/approvals/inbox?filter=approved",
                       "inbox_l1")

    assert pending["items"] == []
    assert [c["reference_id"] for c in approved["items"]] == [o.id]
    card = approved["items"][0]
    assert card["my_action"] == "APPROVE"
    assert card["my_action_at"]
    assert card["my_remarks"] == "Looks fine"
    # The document itself is still moving -- level 2 has it now.
    assert card["status"] == "PENDING"
    assert approved["counts"] == {"all": 1, "pending": 0, "approved": 1,
                                  "rejected": 0}


def test_rejected_filter_lists_my_own_rejections(db, client, env):
    o = _submitted_order(env)
    MaintenanceOrderService().reject(o.id, user=env["l1"],
                                     remarks="Wrong vehicle")
    _db.session.commit()

    _, rejected = _get(client, "/api/v1/approvals/inbox?filter=rejected",
                       "inbox_l1")
    assert [c["reference_id"] for c in rejected["items"]] == [o.id]
    assert rejected["items"][0]["status"] == "REJECTED"
    assert rejected["items"][0]["my_action"] == "REJECT"


def test_someone_elses_decision_is_not_in_my_history(db, client, env):
    o = _submitted_order(env)
    MaintenanceOrderService().reject(o.id, user=env["l1"])
    _db.session.commit()
    # Both are off the path for this now-closed document: L2 never got
    # it, the outsider never had it. Neither may see L1's decision.
    for who in ("inbox_l2", "inbox_out"):
        _, body = _get(client, "/api/v1/approvals/inbox", who)
        assert body["items"] == [], who


def test_search_matches_reference_or_plate(db, client, env):
    o = _submitted_order(env)
    _, hit = _get(client,
                  f"/api/v1/approvals/inbox?q={o.document_number[-4:]}",
                  "inbox_l1")
    _, plate = _get(client, "/api/v1/approvals/inbox?q=abc-12", "inbox_l1")
    _, miss = _get(client, "/api/v1/approvals/inbox?q=zzz-none",
                   "inbox_l1")
    assert len(hit["items"]) == 1
    assert len(plate["items"]) == 1
    assert miss["items"] == []


def test_unknown_filter_is_a_bad_request(db, client, env):
    status, _ = _get(client, "/api/v1/approvals/inbox?filter=everything",
                     "inbox_l1")
    assert status == 400


# ----------------------------------------------------------------- card

def test_card_for_a_pending_approver_includes_chain_and_can_act(
        db, client, env):
    o = _submitted_order(env)
    status, card = _get(client,
                        f"/api/v1/approvals/maintenance_orders/{o.id}/card",
                        "inbox_l1")
    assert status == 200
    assert card["can_act"] is True
    assert [s["status"] for s in card["approval_chain"]] == [
        "CURRENT", "WAITING"]
    assert card["is_final_level"] is False


def test_card_after_acting_still_opens_but_cannot_act(db, client, env):
    o = _submitted_order(env)
    MaintenanceOrderService().approve(o.id, user=env["l1"])
    _db.session.commit()
    status, card = _get(client,
                        f"/api/v1/approvals/maintenance_orders/{o.id}/card",
                        "inbox_l1")
    assert status == 200
    assert card["can_act"] is False
    assert card["approval_chain"][0]["status"] == "APPROVED"
    assert card["approval_chain"][0]["acted_at"]


def test_card_is_404_to_someone_with_no_part_in_it(db, client, env):
    """404, not 403: the ids are sequential, and a different answer for
    "exists but not yours" would let anyone walk them."""
    o = _submitted_order(env)
    status, _ = _get(client,
                     f"/api/v1/approvals/maintenance_orders/{o.id}/card",
                     "inbox_out")
    assert status == 404


def test_card_for_unknown_table_is_404(db, client, env):
    status, _ = _get(client, "/api/v1/approvals/unicorns/1/card",
                     "inbox_l1")
    assert status == 404


# ------------------------------------------------------------- amount

def test_card_amount_is_the_documents_own_figure_never_a_zero_fallback():
    """Found seeding demo data: BaseTransactionService._approval_amount
    falls back to Decimal("0") when a document has none of its known
    money fields -- trip tickets and ATDs have no amount at all, and
    tire/battery keep theirs in `cost` + `labor_cost`, which it does not
    read. Shown on a card, that 0 reads as "this costs nothing". The card
    therefore shows the document's own figure, or nothing."""
    from types import SimpleNamespace
    from app.modules.api.approvals import _document_amount

    assert _document_amount(SimpleNamespace(estimated_cost=12500)) == 12500.0
    assert _document_amount(SimpleNamespace(amount="3200.50")) == 3200.5
    # Tire / battery: the item plus the labour on the same document.
    assert _document_amount(SimpleNamespace(cost=8000, labor_cost=750)) == 8750.0
    assert _document_amount(SimpleNamespace(cost=6200, labor_cost=None)) == 6200.0
    assert _document_amount(SimpleNamespace(or_cr_cost=1800)) == 1800.0
    # A trip ticket carries no money: no amount, not 0.
    assert _document_amount(SimpleNamespace(destination="Cebu")) is None
    assert _document_amount(None) is None


def test_timestamps_say_they_are_utc(db, client, env):
    """Stored naive in UTC (BaseModel.utcnow). Sent naive, a phone in
    Manila reads 01:22 as 1:22 AM local -- eight hours wrong, and
    "Pending for ..." skewed by the same eight hours."""
    o = _submitted_order(env)
    MaintenanceOrderService().approve(o.id, user=env["l1"])
    _db.session.commit()
    _, body = _get(client, "/api/v1/approvals/inbox?filter=approved",
                   "inbox_l1")
    card = body["items"][0]
    for key in ("submitted_at", "my_action_at"):
        assert card[key].endswith("+00:00"), key
    _, detail = _get(client,
                     f"/api/v1/approvals/maintenance_orders/{o.id}/card",
                     "inbox_l2")
    assert detail["pending_since"].endswith("+00:00")
    assert detail["approval_chain"][0]["acted_at"].endswith("+00:00")
