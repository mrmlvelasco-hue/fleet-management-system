"""Maintenance Invoice API.

Flask has nine invoice routes -- new, detail, add line, remove line,
submit, approve, reject, return, reopen. The API had ZERO, so the whole
module was unreachable from React.

That is what blocks the client's testing right now: an MO cannot be
completed without its actual cost, and the actual cost comes from
invoices. The order sits IN_PROGRESS with nowhere to enter what was
spent.

Lines carry `expense_category` and `charged_to`, which is where the MO
Cost Summary's category rows come from -- so this is also the missing
half of the panel already built.
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
from app.modules.transactions.maintenance_order.service import (
    MaintenanceOrderService)
from app.modules.user_management.models import Permission, Role, User


@pytest.fixture()
def inv_env(db):
    sync_permissions()
    db.session.commit()

    role = Role(name="Invoice Clerk")
    role.permissions = Permission.query.filter(
        Permission.code.in_(["maintenanceinvoice.view",
                             "maintenanceinvoice.create",
                             "maintenanceinvoice.update",
                             "maintenanceorder.view"])).all()
    user = User(username="invclerk", email="ic@e.com",
                password_hash=hash_password("secret123"), is_active=True)
    user.roles = [role]

    viewer_role = Role(name="Invoice Viewer")
    viewer_role.permissions = Permission.query.filter(
        Permission.code == "maintenanceinvoice.view").all()
    viewer = User(username="invviewer", email="iv@e.com",
                  password_hash=hash_password("secret123"), is_active=True)
    viewer.roles = [viewer_role]
    db.session.add_all([role, user, viewer_role, viewer])

    branch = BranchService().create(code="BR-IV", name="Invoice Branch")
    vt = VehicleTypeService().create(code="LV-IV", name="Light",
                                     category="LIGHT")
    mt = MaintenanceTypeService().create(code="IV-MT", name="PMS",
                                         category="PM")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2024,
        branch_id=branch.id, conduction_number="IV-000")
    vendor = VendorService().create(code="VEND-IV", name="ABC Motors",
                                    vendor_type="SERVICES",
                                    email="abc@v.com", contact_person="J",
                                    phone="0917")
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
    return {"order": order, "vendor": vendor, "user": user}


def _token(client, username="invclerk"):
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


def _header(client, token, inv_env, number="INV-001"):
    return _post(client, "/api/v1/invoices", token, {
        "maintenance_order_id": inv_env["order"].id,
        "vendor_id": inv_env["vendor"].id,
        "invoice_number": number,
        "invoice_date": date.today().isoformat(),
        "vat_type": "VAT_EXCLUSIVE", "vat_percentage": 12,
    })


# ── Auth ────────────────────────────────────────────────────────────────────

def test_create_rejects_anonymous(db, client, inv_env):
    assert client.post("/api/v1/invoices", json={}).status_code == 401


def test_create_requires_the_create_permission(db, client, inv_env):
    status, _ = _header(client, _token(client, "invviewer"), inv_env)
    assert status == 403


# ── Header ──────────────────────────────────────────────────────────────────

def test_create_returns_the_header(db, client, inv_env):
    status, body = _header(client, _token(client), inv_env)
    assert status == 201, body
    assert body["invoice_number"] == "INV-001"
    # Auto-numbered by the engine; the client cannot derive it.
    assert "document_number" in body
    assert body["status"] == "RECORDED"


def test_the_date_is_coerced(db, client, inv_env):
    """JSON has no date type. Vehicles shipped a 500 on exactly this
    before the coercion helper existed; invoices use the same helper
    rather than repeating it."""
    status, body = _header(client, _token(client), inv_env)
    assert status == 201, body
    assert body["invoice_date"] == date.today().isoformat()


def test_a_malformed_date_is_a_field_error(db, client, inv_env):
    status, body = _post(client, "/api/v1/invoices", _token(client), {
        "maintenance_order_id": inv_env["order"].id,
        "vendor_id": inv_env["vendor"].id, "invoice_number": "INV-BAD",
        "invoice_date": "31/12/2026",
    })
    assert status == 400, body
    assert "invoice_date" in body.get("fields", {})


def test_vat_defaults_match_flask(db, client, inv_env):
    """Flask defaults vat_type VAT_EXCLUSIVE, vat_percentage 12,
    currency PHP. An invoice created through React must not be shaped
    differently from one created through the form."""
    status, body = _post(client, "/api/v1/invoices", _token(client), {
        "maintenance_order_id": inv_env["order"].id,
        "vendor_id": inv_env["vendor"].id, "invoice_number": "INV-DEF",
        "invoice_date": date.today().isoformat(),
    })
    assert status == 201, body
    assert body["vat_type"] == "VAT_EXCLUSIVE"
    assert str(body["vat_percentage"]) in ("12", "12.00")
    assert body["currency"] == "PHP"


# ── Lines ───────────────────────────────────────────────────────────────────

def test_add_line_recalculates_the_header(db, client, inv_env):
    """_recalculate_summary is the service's. The API must not compute
    totals itself -- a second implementation of money arithmetic is a
    second answer."""
    _status, inv = _header(client, _token(client), inv_env)
    status, body = _post(client, f"/api/v1/invoices/{inv['id']}/lines",
                         _token(client), {
                             "part_description": "Engine oil",
                             "expense_category": "PARTS",
                             "charged_to": "COMPANY",
                             "quantity": 2, "unit_cost": 1000})
    assert status == 201, body
    _status, fresh = _get(client, f"/api/v1/invoices/{inv['id']}",
                          _token(client))
    assert float(fresh["total_parts_cost"]) == 2000


def test_expense_category_is_required(db, client, inv_env):
    """It drives the MO Cost Summary's category rows. A line without one
    would land in the total and under no row -- the exact breakage the
    grouped summary was built to avoid."""
    _status, inv = _header(client, _token(client), inv_env)
    status, _ = _post(client, f"/api/v1/invoices/{inv['id']}/lines",
                      _token(client), {"part_description": "Mystery",
                                       "charged_to": "COMPANY",
                                       "quantity": 1, "unit_cost": 10})
    assert status == 400


def test_charged_to_is_required(db, client, inv_env):
    """An approver seeing INSURANCE_CLAIM rather than COMPANY is looking
    at a different decision. Defaulting it would silently charge the
    company for something claimable."""
    _status, inv = _header(client, _token(client), inv_env)
    status, _ = _post(client, f"/api/v1/invoices/{inv['id']}/lines",
                      _token(client), {"part_description": "Tow",
                                       "expense_category": "TOWING",
                                       "quantity": 1, "unit_cost": 10})
    assert status == 400


def test_remove_line_recalculates(db, client, inv_env):
    t = _token(client)
    _status, inv = _header(client, t, inv_env)
    _status, line = _post(client, f"/api/v1/invoices/{inv['id']}/lines", t, {
        "part_description": "Engine oil", "expense_category": "PARTS",
        "charged_to": "COMPANY", "quantity": 1, "unit_cost": 500})
    r = client.delete(f"/api/v1/invoices/{inv['id']}/lines/{line['id']}",
                      headers={"Authorization": f"Bearer {t}"})
    assert r.status_code == 200
    _status, fresh = _get(client, f"/api/v1/invoices/{inv['id']}", t)
    assert float(fresh["total_parts_cost"]) == 0


# ── Listing for an order ────────────────────────────────────────────────────

def test_invoices_are_listed_for_their_order(db, client, inv_env):
    """The MO screen's Invoices section. Without it a user cannot see
    what has already been recorded and would enter the same invoice
    twice."""
    t = _token(client)
    _header(client, t, inv_env, "INV-A")
    _header(client, t, inv_env, "INV-B")
    _status, body = _get(
        client,
        f"/api/v1/maintenance-orders/{inv_env['order'].id}/invoices", t)
    assert {i["invoice_number"] for i in body["items"]} == {"INV-A", "INV-B"}


def test_an_order_with_no_invoices_lists_empty(db, client, inv_env):
    _status, body = _get(
        client,
        f"/api/v1/maintenance-orders/{inv_env['order'].id}/invoices",
        _token(client))
    assert body["items"] == []


def test_detail_404s_for_a_missing_invoice(db, client, inv_env):
    status, _ = _get(client, "/api/v1/invoices/999999", _token(client))
    assert status == 404


def test_a_line_cannot_be_deleted_through_another_invoice(db, client,
                                                          inv_env):
    """The service's remove_line takes only a line id and does not check
    which invoice it belongs to. The URL asserts a relationship, so the
    API verifies it -- otherwise /invoices/5/lines/99 would delete a
    line from invoice 7 and silently change that invoice's totals."""
    t = _token(client)
    _status, a = _header(client, t, inv_env, "INV-OWN-A")
    _status, b = _header(client, t, inv_env, "INV-OWN-B")
    _status, line = _post(client, f"/api/v1/invoices/{b['id']}/lines", t, {
        "part_description": "Oil", "expense_category": "PARTS",
        "charged_to": "COMPANY", "quantity": 1, "unit_cost": 100})

    r = client.delete(f"/api/v1/invoices/{a['id']}/lines/{line['id']}",
                      headers={"Authorization": f"Bearer {t}"})
    assert r.status_code == 404

    _status, fresh = _get(client, f"/api/v1/invoices/{b['id']}", t)
    assert len(fresh["lines"]) == 1
