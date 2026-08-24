"""Maintenance Order cost summary, grouped by expense category.

The MO detail screen shows a Cost Summary. The mockup drew three fixed
rows -- Parts, Labor, Shop supplies -- and only the first two exist as
stored fields:

    total_parts_cost = sum(line_amount where expense_category == "PARTS")
    total_labor_cost = sum(line_amount where expense_category == "LABOR")

The category vocabulary has NINE values: PARTS, LABOR, TIRES, BATTERY,
OIL, LUBRICANTS, EXTERNAL_SERVICES, TOWING, MISC. So seven of them are
invisible in the header. A line categorised TOWING lands in net_amount
and total_invoice_amount but under neither Parts nor Labor.

A three-row breakdown would therefore present rows that DO NOT SUM TO
THE TOTAL, on a document that authorises payment. Someone reconciling
it finds money that belongs to no row and no explanation of where it
went.

So this groups by the category actually used, at read time, and returns
only categories with a value. Every row is real, the rows always
reconcile to the total, and no schema change or migration is needed --
`expense_category` is already required on every line.

"Shop supplies", if the client uses that term for something specific,
becomes a LOOKUP ENTRY named that, not a hardcoded row summing
categories nobody chose.
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
from app.modules.master_data.vendor.service import VendorService
from app.modules.transactions.maintenance_invoice.service import (
    MaintenanceInvoiceService)
from app.modules.transactions.maintenance_order.service import (
    MaintenanceOrderService)
from app.modules.user_management.models import Permission, Role, User


@pytest.fixture()
def cs_env(db):
    sync_permissions()
    db.session.commit()

    role = Role(name="MO Cost Viewer")
    role.permissions = Permission.query.filter(
        Permission.code == "maintenanceorder.view").all()
    user = User(username="costuser", email="cu@e.com",
                password_hash=hash_password("secret123"), is_active=True)
    user.roles = [role]
    db.session.add_all([role, user])

    branch = BranchService().create(code="BR-CS", name="Cost Branch")
    vt = VehicleTypeService().create(code="LV-CS", name="Light",
                                     category="LIGHT")
    mt = MaintenanceTypeService().create(code="CS-MT", name="PMS",
                                         category="PM")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2024,
        branch_id=branch.id, conduction_number="CS-000")
    vendor = VendorService().create(code="VEND-CS", name="ABC Motors",
                                    vendor_type="SERVICES",
                                    email="abc@vendor.com",
                                    contact_person="Juan", phone="0917")
    from app.modules.document_config.models import DocumentType
    for code in ["MO", "INV"]:
        if DocumentType.query.filter_by(code=code).first() is None:
            DocumentTypeService().create(code=code, name=code,
                                         requires_approval=False,
                                         auto_numbering=True)
            dt = DocumentType.query.filter_by(code=code).first()
            NumberingSchemeService().create(
                document_type_id=dt.id, prefix=code, include_year=True,
                digit_count=6, reset_policy="YEARLY")
    order = MaintenanceOrderService().create(
        vehicle_id=vehicle.id, maintenance_type_id=mt.id,
        scheduled_date=date.today(), user=None)
    db.session.commit()
    return {"user": user, "order": order, "vendor": vendor}


def _token(client, username="costuser"):
    r = client.post("/api/v1/auth/token",
                    json={"username": username, "password": "secret123"})
    return json.loads(r.get_data(as_text=True)).get("access_token")


def _summary(client, oid, token):
    r = client.get(f"/api/v1/maintenance-orders/{oid}/cost-summary",
                   headers={"Authorization": f"Bearer {token}"})
    return r.status_code, json.loads(r.get_data(as_text=True))


def _invoice_with(db, cs_env, lines, number="INV-CS-1"):
    svc = MaintenanceInvoiceService()
    inv = svc.create(maintenance_order_id=cs_env["order"].id,
                     vendor_id=cs_env["vendor"].id, invoice_number=number,
                     invoice_date=date.today(), vat_type="VAT_EXCLUSIVE",
                     vat_percentage=0, user=None)
    for desc, category, cost in lines:
        svc.add_line(inv.id, part_description=desc,
                     expense_category=category, charged_to="COMPANY",
                     quantity=1, unit_cost=cost)
    db.session.commit()
    return inv


# ── Auth ────────────────────────────────────────────────────────────────────

def test_rejects_anonymous(db, client, cs_env):
    assert client.get(
        f"/api/v1/maintenance-orders/{cs_env['order'].id}/cost-summary"
    ).status_code == 401


# ── Grouping ────────────────────────────────────────────────────────────────

def test_groups_by_the_category_actually_used(db, client, cs_env):
    _invoice_with(db, cs_env, [
        ("Engine oil", "PARTS", 8400),
        ("Mechanic time", "LABOR", 2500),
        ("Flatbed", "TOWING", 1500),
    ])
    _status, body = _summary(client, cs_env["order"].id, _token(client))
    rows = {r["category"]: r["amount"] for r in body["by_category"]}
    assert rows == {"PARTS": "8400.00", "LABOR": "2500.00",
                    "TOWING": "1500.00"}


def test_rows_reconcile_to_the_total(db, client, cs_env):
    """The property the fixed three-row version could not hold. A
    breakdown whose rows do not sum to the total leaves money belonging
    to no row, on a document that authorises payment."""
    _invoice_with(db, cs_env, [
        ("Engine oil", "PARTS", 8400),
        ("Mechanic time", "LABOR", 2500),
        ("Flatbed", "TOWING", 1500),
        ("Rags", "MISC", 100),
    ])
    _status, body = _summary(client, cs_env["order"].id, _token(client))
    rows_total = sum(float(r["amount"]) for r in body["by_category"])
    assert rows_total == float(body["net_amount"])


def test_a_category_with_no_lines_is_absent_not_zero(db, client, cs_env):
    """A zero row invites the reader to wonder what was meant to be
    there. Absent says the invoice simply had none."""
    _invoice_with(db, cs_env, [("Engine oil", "PARTS", 8400)])
    _status, body = _summary(client, cs_env["order"].id, _token(client))
    assert [r["category"] for r in body["by_category"]] == ["PARTS"]


def test_categories_carry_their_lookup_description(db, client, cs_env):
    """The screen shows "Parts", not "PARTS". Resolved server-side from
    the EXPENSE_CATEGORY lookup, so a client renaming a category in
    Lookup Maintenance sees it change here too rather than against a
    hardcoded map."""
    _invoice_with(db, cs_env, [("Engine oil", "PARTS", 8400)])
    _status, body = _summary(client, cs_env["order"].id, _token(client))
    assert body["by_category"][0]["label"] == "Parts"


def test_totals_span_every_invoice_on_the_order(db, client, cs_env):
    """An MO can carry several invoices -- parts from one supplier,
    labour from another. A summary reading only the first would understate
    the spend being approved."""
    _invoice_with(db, cs_env, [("Engine oil", "PARTS", 8400)], "INV-CS-1")
    _invoice_with(db, cs_env, [("Mechanic time", "LABOR", 2500)], "INV-CS-2")
    _status, body = _summary(client, cs_env["order"].id, _token(client))
    rows = {r["category"]: r["amount"] for r in body["by_category"]}
    assert rows == {"PARTS": "8400.00", "LABOR": "2500.00"}


def test_an_order_with_no_invoices_returns_empty_not_an_error(db, client,
                                                              cs_env):
    """Every MO has no invoices until work is done. That is the normal
    early state, not a fault."""
    status, body = _summary(client, cs_env["order"].id, _token(client))
    assert status == 200
    assert body["by_category"] == []
    assert body["net_amount"] == "0.00"


def test_estimated_cost_is_carried_alongside(db, client, cs_env):
    """The approver compares estimate against actual. Returning only
    actuals would make the summary unreadable before any invoice exists,
    which is exactly when approval happens."""
    cs_env["order"].estimated_cost = 9500
    db.session.commit()
    _status, body = _summary(client, cs_env["order"].id, _token(client))
    assert body["estimated_cost"] == "9500.00"


def test_vat_and_gross_are_reported_separately(db, client, cs_env):
    """Category rows are NET. VAT belongs to the invoice, not to a
    category, and folding it into the rows would make them disagree with
    the invoice they came from."""
    svc = MaintenanceInvoiceService()
    inv = svc.create(maintenance_order_id=cs_env["order"].id,
                     vendor_id=cs_env["vendor"].id,
                     invoice_number="INV-VAT-1", invoice_date=date.today(),
                     vat_type="VAT_EXCLUSIVE", vat_percentage=12, user=None)
    svc.add_line(inv.id, part_description="Engine oil",
                 expense_category="PARTS", charged_to="COMPANY",
                 quantity=1, unit_cost=1000)
    db.session.commit()

    _status, body = _summary(client, cs_env["order"].id, _token(client))
    assert body["by_category"][0]["amount"] == "1000.00"
    assert float(body["total_vat"]) > 0
    assert float(body["gross_amount"]) > float(body["net_amount"])


def test_404_for_an_order_outside_scope(db, client, cs_env):
    status, _ = _summary(client, 999999, _token(client))
    assert status == 404


# ── Approval chain on MO detail ─────────────────────────────────────────────

def test_mo_detail_carries_the_approval_chain(db, client, cs_env):
    """The MO detail screen's right panel shows the approval workflow --
    who approved, who is next, and what they said.

    ATD already exposes exactly this, built from
    ApprovalEngine.get_approval_chain(). Mirrored rather than
    reinvented: two shapes for one concept would mean two React
    components rendering the same workflow differently, and an approver
    seeing a different chain on ATD than on MO has no way to know which
    is right.
    """
    r = client.get(f"/api/v1/maintenance-orders/{cs_env['order'].id}",
                   headers={"Authorization": f"Bearer {_token(client)}"})
    assert r.status_code == 200
    body = json.loads(r.get_data(as_text=True))
    for key in ("approval_chain", "approval_instance_status",
                "approval_current_level", "can_act"):
        assert key in body, f"{key} missing from MO detail"


def test_an_unsubmitted_order_has_an_empty_chain_not_an_error(db, client,
                                                              cs_env):
    """A DRAFT order has no approval instance. That is the normal state
    of every order before submission, so it returns an empty chain --
    the panel then renders "not yet submitted" rather than failing."""
    r = client.get(f"/api/v1/maintenance-orders/{cs_env['order'].id}",
                   headers={"Authorization": f"Bearer {_token(client)}"})
    body = json.loads(r.get_data(as_text=True))
    assert body["approval_chain"] == []
    assert body["approval_instance_status"] is None
    assert body["can_act"] is False


def test_can_act_is_false_for_a_non_approver(db, client, cs_env):
    """Drives whether the decision buttons render. A viewer offered
    Approve and Reject that then 403 has been told the system is
    broken."""
    r = client.get(f"/api/v1/maintenance-orders/{cs_env['order'].id}",
                   headers={"Authorization": f"Bearer {_token(client)}"})
    assert json.loads(r.get_data(as_text=True))["can_act"] is False


# ── Approval actions ────────────────────────────────────────────────────────

def _post(client, path, token, payload=None):
    r = client.post(path, json=payload or {},
                    headers={"Authorization": f"Bearer {token}"})
    body = r.get_data(as_text=True)
    return r.status_code, (json.loads(body) if body else {})


@pytest.mark.parametrize("action", ["approve", "reject", "return"])
def test_approval_actions_reject_anonymous(db, client, cs_env, action):
    assert client.post(
        f"/api/v1/maintenance-orders/{cs_env['order'].id}/{action}"
    ).status_code == 401


@pytest.mark.parametrize("action", ["approve", "reject", "return"])
def test_approval_actions_exist(db, client, cs_env, action):
    """Parity: every action Flask offers must be reachable from React.

    Flask has approve, reject and return on the MO detail screen. The
    API had none, so a React approver could read the workflow and not
    act on it -- the screen showed a decision it could not carry out.

    A DRAFT order has no approval instance, so the engine refuses. 409
    (conflict) is the honest answer: the request was well-formed and the
    document is in the wrong state, which is different from the route
    not existing.
    """
    status, _ = _post(
        client, f"/api/v1/maintenance-orders/{cs_env['order'].id}/{action}",
        _token(client))
    assert status != 404, f"/{action} is not routed"
    assert status in (200, 409, 403)


@pytest.mark.parametrize("action", ["approve", "reject", "return"])
def test_a_non_approver_cannot_act(db, client, cs_env, action):
    """Eligibility is the ENGINE's, not the route's. The Jinja route
    gates on maintenanceorder.view and lets the engine decide who may
    approve -- because holding view is not the same as being on the
    approval path. Re-implementing that check here would be a second
    definition of who an approver is."""
    status, _ = _post(
        client, f"/api/v1/maintenance-orders/{cs_env['order'].id}/{action}",
        _token(client))
    # Never a 200: this user is not on any approval path.
    assert status != 200


def test_remarks_are_carried_through(db, client, cs_env):
    """A rejection or return without its reason leaves the requester
    knowing they must act and not what to change. The field must reach
    the engine, not be dropped by the API layer."""
    import inspect

    from app.modules.api import maintenance_orders as mod

    source = inspect.getsource(mod)
    # Every action routes through ONE shared helper, and that helper
    # passes remarks. An earlier version of this test counted three
    # copies of p.get("remarks") -- which would have FAILED the better
    # design and passed the duplicated one. Asserting the shape rather
    # than the repetition.
    for method in ("approve", "reject", "return_document", "resubmit"):
        assert f'"{method}"' in source, f"{method} not routed"
    assert 'remarks=p.get("remarks")' in source, (
        "remarks not passed to the engine")


def test_detail_says_whether_the_reader_is_the_requester(db, client, cs_env):
    """Flask shows Cancel only to the requester
    (`item.requester.id == current_user.id`). React could not apply that
    rule: the payload carried neither the requester's id nor a flag.

    Sent as a DECISION rather than as an id to compare. The client
    should not be doing identity arithmetic to decide whether a
    destructive button appears -- and comparing ids client-side would
    also mean shipping the requester's user id to every reader.
    """
    r = client.get(f"/api/v1/maintenance-orders/{cs_env['order'].id}",
                   headers={"Authorization": f"Bearer {_token(client)}"})
    body = json.loads(r.get_data(as_text=True))
    assert "is_requester" in body
    # This order was created with user=None, so nobody is its requester.
    assert body["is_requester"] is False


def test_detail_carries_the_approval_instance_presence(db, client, cs_env):
    """Flask gates Submit on `not item.approval_instance` -- an order
    already in the workflow must not be submittable twice. React needs
    to know the instance EXISTS, which is different from knowing its
    status: a rejected order has a status and still must not resubmit
    via Submit."""
    r = client.get(f"/api/v1/maintenance-orders/{cs_env['order'].id}",
                   headers={"Authorization": f"Bearer {_token(client)}"})
    assert "has_approval_instance" in json.loads(r.get_data(as_text=True))
