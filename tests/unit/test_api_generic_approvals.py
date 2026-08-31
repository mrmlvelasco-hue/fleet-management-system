"""Module-agnostic approval endpoints.

The master prompt says the Approval Engine must be reusable across all
modules and that approval logic shall never be hardcoded. That holds in
the SERVICE layer -- _approval_action is already a shared helper -- but
the API layer re-hardcodes it once per module:
/maintenance-orders/<id>/approve, /purchase-requests/<id>/approve,
/atd/<id>/approve, and so on across eight modules.

That is affordable for the web frontend, which ships whenever we say so.
It is not affordable for the mobile app: a client that has to know every
module by name needs a new App Store build every time a ninth
transaction type is added. These endpoints let the app know APPROVALS
rather than modules, so a new document type appears in the inbox and is
actionable with no rebuild.

Deliberately NOT a new engine. Every route here resolves to the same
per-module service call the existing routes make, so behaviour cannot
drift between the two -- an approval made from a phone and one made
from the web take the same path.
"""
import json
from datetime import date

import pytest

from app.core.security.password import hash_password
from app.core.security.registry import sync_permissions
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.reference.service import (
    MaintenanceTypeService, VehicleTypeService)
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.transactions.maintenance_order.service import (
    MaintenanceOrderService)
from app.modules.user_management.models import Permission, Role, User


def _ensure_doc_type(code):
    from app.modules.document_config.models import DocumentType
    from app.modules.document_config.service import (
        DocumentTypeService, NumberingSchemeService)
    if DocumentType.query.filter_by(code=code).first() is None:
        DocumentTypeService().create(code=code, name=code,
                                     requires_approval=False,
                                     auto_numbering=True)
        dt = DocumentType.query.filter_by(code=code).first()
        NumberingSchemeService().create(document_type_id=dt.id, prefix=code,
                                        include_year=True, digit_count=6,
                                        reset_policy="YEARLY")


@pytest.fixture()
def env(db):
    sync_permissions()
    db.session.commit()

    role = Role(name="Generic Approver")
    role.permissions = Permission.query.filter(
        Permission.code.in_(["maintenanceorder.view",
                             "purchaserequest.view"])).all()
    approver = User(username="genapprover", email="ga@e.com",
                    password_hash=hash_password("secret123"), is_active=True)
    approver.roles = [role]
    db.session.add_all([role, approver])

    branch = BranchService().create(code="BR-GA", name="GA Branch")
    vt = VehicleTypeService().create(code="LV-GA", name="Light",
                                     category="LIGHT")
    mt = MaintenanceTypeService().create(code="GA-MT", name="PMS",
                                         category="PM")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2024,
        branch_id=branch.id, conduction_number="GA-000")
    _ensure_doc_type("MO")
    db.session.commit()
    return {"approver": approver, "branch": branch, "vt": vt, "mt": mt,
            "vehicle": vehicle}


def _token(client, username="genapprover"):
    r = client.post("/api/v1/auth/token",
                    json={"username": username, "password": "secret123"})
    return json.loads(r.get_data(as_text=True)).get("access_token")


def _get(client, url, token):
    r = client.get(url, headers={"Authorization": f"Bearer {token}"})
    return r.status_code, json.loads(r.get_data(as_text=True))


def _post(client, url, token, body=None):
    r = client.post(url, json=body or {},
                    headers={"Authorization": f"Bearer {token}"})
    return r.status_code, json.loads(r.get_data(as_text=True))


def _draft_order(env):
    return MaintenanceOrderService().create(
        vehicle_id=env["vehicle"].id, maintenance_type_id=env["mt"].id,
        scheduled_date=date.today(), user=env["approver"],
        description="Generic approval test", estimated_cost=1000)


# --------------------------------------------------------------- guards

def test_rejects_anonymous(db, client, env):
    o = _draft_order(env)
    assert client.get(
        f"/api/v1/approvals/maintenance_orders/{o.id}/summary"
    ).status_code == 401


def test_unknown_reference_table_is_404_not_500(db, client, env):
    """A table the registry does not carry must read as "no such thing",
    not as a server fault -- the mobile client will send whatever the
    inbox gave it, and a 500 would look like an outage."""
    status, _ = _get(client, "/api/v1/approvals/unicorns/1/summary",
                     _token(client))
    assert status == 404


def test_unknown_action_is_refused(db, client, env):
    """Only the engine's three decisions are dispatchable. Without an
    allow-list, the action name reaches getattr() on the service and any
    method on it becomes callable over HTTP."""
    o = _draft_order(env)
    status, _ = _post(client,
                      f"/api/v1/approvals/maintenance_orders/{o.id}/delete",
                      _token(client))
    assert status in (400, 404, 405)


def test_missing_record_is_404(db, client, env):
    status, _ = _get(client, "/api/v1/approvals/maintenance_orders/9999/summary",
                     _token(client))
    assert status == 404


# -------------------------------------------------------------- summary

def test_summary_names_the_document_without_module_knowledge(db, client, env):
    """The card an approver decides from. Every field here is one the
    mobile app can render for ANY document type, which is the whole
    point -- a ninth module must not need a new screen."""
    o = _draft_order(env)
    status, body = _get(
        client, f"/api/v1/approvals/maintenance_orders/{o.id}/summary",
        _token(client))
    assert status == 200
    assert body["reference_table"] == "maintenance_orders"
    assert body["reference_id"] == o.id
    assert body["document_number"] == o.document_number
    assert body["document_label"] == "Maintenance Order"
    # The figure approval routing resolves against. An approver deciding
    # without it is deciding blind.
    assert body["amount"] == 1000
    assert "status" in body
    assert "requested_by" in body


def test_summary_carries_detail_lines_for_context(db, client, env):
    """Label/value pairs rather than typed fields, so the app renders
    them without knowing what a Maintenance Order is."""
    o = _draft_order(env)
    _status, body = _get(
        client, f"/api/v1/approvals/maintenance_orders/{o.id}/summary",
        _token(client))
    labels = [d["label"] for d in body["details"]]
    assert "Vehicle" in labels
    assert all(set(d) == {"label", "value"} for d in body["details"])


# --------------------------------------------------------------- actions

def test_generic_route_answers_identically_to_the_module_route(
        db, client, env):
    """Not a second engine.

    Asserted as an EQUIVALENCE rather than by driving one happy-path
    approval: what matters is that the two routes cannot diverge, and a
    single successful approve would only show that one path works on one
    day. Feeding both the same document in the same state and requiring
    the same answer catches a drift in either direction -- including the
    engine changing under both.
    """
    o = _draft_order(env)
    token = _token(client)

    generic_status, generic_body = _post(
        client, f"/api/v1/approvals/maintenance_orders/{o.id}/approve",
        token, {"remarks": "From the yard."})
    module_status, module_body = _post(
        client, f"/api/v1/maintenance-orders/{o.id}/approve",
        token, {"remarks": "From the yard."})

    assert generic_status == module_status
    assert generic_body.get("message") == module_body.get("message")


def test_action_reaches_the_modules_own_service(db, client, env, monkeypatch):
    """The dispatch itself: the registered service's method is the thing
    called, with the id, the acting user and the remarks. If this ever
    routes somewhere else, an approval from a phone stops meaning what
    an approval from the web means."""
    from app.modules.transactions.maintenance_order.service import (
        MaintenanceOrderService)

    seen = {}

    def fake_approve(self, oid, user=None, remarks=None):
        seen.update(oid=oid, user=getattr(user, "username", None),
                    remarks=remarks)

    monkeypatch.setattr(MaintenanceOrderService, "approve", fake_approve,
                        raising=False)

    o = _draft_order(env)
    _post(client, f"/api/v1/approvals/maintenance_orders/{o.id}/approve",
          _token(client), {"remarks": "Approved from the yard."})

    assert seen == {"oid": o.id, "user": "genapprover",
                    "remarks": "Approved from the yard."}


def test_engine_refusal_is_409_not_400(db, client, env):
    """A DRAFT cannot be approved. The request was well formed and the
    DOCUMENT is in the wrong state -- calling that a bad request sends
    someone checking a payload that is fine."""
    o = _draft_order(env)
    status, body = _post(
        client, f"/api/v1/approvals/maintenance_orders/{o.id}/approve",
        _token(client))
    assert status == 409
    assert body.get("message")


def test_return_requires_remarks(db, client, env):
    """A return with no reason tells the requester only that something
    is wrong and leaves them guessing what. Enforced server-side because
    the mobile client is not the only caller."""
    o = _draft_order(env)
    status, _ = _post(
        client, f"/api/v1/approvals/maintenance_orders/{o.id}/return",
        _token(client), {"remarks": "   "})
    assert status == 400


def test_permission_is_the_modules_own(db, client, env):
    """A user with no rights over the module cannot act on its
    documents through the generic route -- otherwise this endpoint is a
    way around every per-module permission in the system."""
    stranger = User(username="stranger", email="s@e.com",
                    password_hash=hash_password("secret123"), is_active=True)
    db.session.add(stranger)
    db.session.commit()

    o = _draft_order(env)
    status, _ = _post(
        client, f"/api/v1/approvals/maintenance_orders/{o.id}/approve",
        _token(client, "stranger"))
    assert status == 403


# -------------------------------------------------------------- registry

def test_every_registered_table_resolves(db, client, env):
    """The registry is hand-maintained, exactly like _REACT_ROUTE_MAP,
    and that map has already drifted once in this codebase -- a PR
    detail screen existed for two commits while the map still said the
    task was not available. This asserts every entry actually imports
    and points at a real model, so a typo fails here rather than as a
    500 on a phone in the field."""
    from app.modules.api.approvals import REGISTRY, resolve_entry

    assert REGISTRY, "registry must not be empty"
    for table in REGISTRY:
        entry = resolve_entry(table)
        assert entry is not None
        assert entry.model is not None
        assert entry.service is not None
        assert entry.label
        assert entry.permission.endswith(".view")
