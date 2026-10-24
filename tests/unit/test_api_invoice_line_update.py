"""Correcting an invoice line that was encoded wrongly (2026-10-24).

Reported from UAT: a line entered with the wrong quantity, unit cost or
description could only be deleted and re-keyed. The API had POST (add)
and DELETE (remove) for a line but no update at all, so there was
nothing for an Edit control to call.

What this pins:

  * the correction lands on the SAME row -- one line in, one line out,
    the id unchanged -- rather than a delete-and-re-add that would
    break the row's audit history and its sort order;
  * the money is recomputed by the SAME rules the add path uses
    (MaintenanceInvoiceService._calculate_line / _recalculate_summary),
    for every VAT treatment, so an edited invoice can never disagree
    with an invoice built line by line;
  * the invoice totals come back in the response, because the screen
    has to show the new total without a page refresh;
  * the URL's invoice id is enforced, so /invoices/7/lines/99 cannot
    reach a line belonging to invoice 5;
  * a locked (approved) invoice and a closed order still refuse the
    change, exactly as adding a line does;
  * the change is audit-logged with before and after values.
"""
import json
from datetime import date
from decimal import Decimal

import pytest

from app.core.security.password import hash_password
from app.core.security.registry import sync_permissions
from app.extensions import db as _db
from app.modules.document_config.service import (
    DocumentTypeService, NumberingSchemeService)
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.reference.service import (
    MaintenanceTypeService, VehicleTypeService)
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.vendor.service import VendorService
from app.modules.transactions.maintenance_invoice.models import (
    MaintenanceInvoice, MaintenanceInvoiceLine)
from app.modules.transactions.maintenance_invoice.service import (
    MaintenanceInvoiceService)
from app.modules.transactions.maintenance_order.service import (
    MaintenanceOrderService)
from app.modules.user_management.models import Permission, Role, User


@pytest.fixture()
def env(db):
    sync_permissions()
    db.session.commit()

    clerk_role = Role(name="Invoice Editor")
    clerk_role.permissions = Permission.query.filter(
        Permission.code.in_(["maintenanceinvoice.view",
                             "maintenanceinvoice.create",
                             "maintenanceinvoice.update",
                             "maintenanceorder.view"])).all()
    clerk = User(username="lineclerk", email="lc@e.com",
                 password_hash=hash_password("secret123"), is_active=True)
    clerk.roles = [clerk_role]

    viewer_role = Role(name="Invoice Reader")
    viewer_role.permissions = Permission.query.filter(
        Permission.code == "maintenanceinvoice.view").all()
    viewer = User(username="linereader", email="lr@e.com",
                  password_hash=hash_password("secret123"), is_active=True)
    viewer.roles = [viewer_role]
    _db.session.add_all([clerk_role, clerk, viewer_role, viewer])

    branch = BranchService().create(code="BR-LU", name="Line Branch")
    vt = VehicleTypeService().create(code="LV-LU", name="Light",
                                     category="LIGHT")
    mt = MaintenanceTypeService().create(code="LU-MT", name="PMS",
                                         category="PM")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2024,
        branch_id=branch.id, conduction_number="LU-000")
    vendor = VendorService().create(code="VEND-LU", name="Alabang Toyota",
                                    vendor_type="SERVICES", email="a@v.com",
                                    contact_person="J", phone="0917")
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
    _db.session.commit()
    return {"order": order, "vendor": vendor, "clerk": clerk}


def _token(client, username="lineclerk"):
    r = client.post("/api/v1/auth/token",
                    json={"username": username, "password": "secret123"})
    return json.loads(r.get_data(as_text=True))["access_token"]


def _invoice(env, *, vat_type="VAT_EXCLUSIVE"):
    return MaintenanceInvoiceService().create(
        maintenance_order_id=env["order"].id, vendor_id=env["vendor"].id,
        invoice_number=f"INV-{vat_type}", invoice_date=date.today(),
        vat_type=vat_type, vat_percentage=12, user=env["clerk"])


def _line(invoice, **over):
    kw = dict(part_description="Oil Filter", expense_category="PARTS",
              charged_to="COMPANY", quantity=4, unit_cost=450, discount=0)
    kw.update(over)
    return MaintenanceInvoiceService().add_line(invoice.id, **kw)


def _put(client, path, token, payload):
    r = client.put(path, json=payload,
                   headers={"Authorization": f"Bearer {token}"})
    body = r.get_data(as_text=True)
    return r.status_code, (json.loads(body) if body else {})


# ── Test A / B: the money is recomputed ─────────────────────────────────────

def test_correcting_the_quantity_recomputes_the_line_and_the_invoice(
        db, client, env):
    """The brief's own example: 4 x 450 corrected to 3 x 450."""
    inv = _invoice(env)
    line = _line(inv)
    assert line.line_amount == Decimal("1800.00")

    status, body = _put(client, f"/api/v1/invoices/{inv.id}/lines/{line.id}",
                        _token(client), {"quantity": "3"})
    assert status == 200, body
    assert body["line"]["line_amount"] == "1350.00"
    assert body["line"]["vat_amount"] == "162.00"
    assert body["line"]["total_amount"] == "1512.00"
    # The invoice the screen has to redraw, in the same response.
    assert body["invoice"]["net_amount"] == "1350.00"
    assert body["invoice"]["total_invoice_amount"] == "1512.00"
    assert body["invoice"]["total_parts_cost"] == "1350.00"


def test_correcting_the_unit_cost_recomputes_everything_below_it(
        db, client, env):
    inv = _invoice(env)
    line = _line(inv)
    status, body = _put(client, f"/api/v1/invoices/{inv.id}/lines/{line.id}",
                        _token(client), {"unit_cost": "500"})
    assert status == 200, body
    assert body["line"]["line_amount"] == "2000.00"
    assert body["invoice"]["total_invoice_amount"] == "2240.00"


def test_a_vat_inclusive_invoice_edits_by_its_own_rule(db, client, env):
    """Not a second formula: the same _calculate_line the add path uses,
    so an edited line matches one keyed in correctly the first time."""
    inv = _invoice(env, vat_type="VAT_INCLUSIVE")
    line = _line(inv, quantity=1, unit_cost=1120)
    _put(client, f"/api/v1/invoices/{inv.id}/lines/{line.id}",
         _token(client), {"quantity": "2"})

    reference = MaintenanceInvoiceService().add_line(
        inv.id, part_description="Same thing keyed correctly",
        expense_category="PARTS", charged_to="COMPANY",
        quantity=2, unit_cost=1120, discount=0)
    edited = _db.session.get(MaintenanceInvoiceLine, line.id)
    assert edited.line_amount == reference.line_amount
    assert edited.vat_amount == reference.vat_amount
    assert edited.total_amount == reference.total_amount


def test_discount_and_category_move_the_money_to_the_right_row(
        db, client, env):
    inv = _invoice(env)
    line = _line(inv)
    status, body = _put(client, f"/api/v1/invoices/{inv.id}/lines/{line.id}",
                        _token(client),
                        {"discount": "300", "expense_category": "LABOR"})
    assert status == 200, body
    assert body["line"]["line_amount"] == "1500.00"
    # Re-filed under LABOR: the parts row must give the money up.
    assert body["invoice"]["total_parts_cost"] == "0.00"
    assert body["invoice"]["total_labor_cost"] == "1500.00"
    assert body["invoice"]["total_discount"] == "300.00"


def test_the_response_carries_the_order_cost_summary(db, client, env):
    """So the Cost Summary panel can redraw from the save itself."""
    inv = _invoice(env)
    line = _line(inv)
    status, body = _put(client, f"/api/v1/invoices/{inv.id}/lines/{line.id}",
                        _token(client), {"quantity": "3"})
    assert status == 200, body
    summary = body["cost_summary"]
    assert summary["total_invoice_amount"] == "1512.00"
    assert [r["category"] for r in summary["by_category"]] == ["PARTS"]
    assert summary["by_category"][0]["amount"] == "1350.00"


# ── Test C: corrected, not duplicated ───────────────────────────────────────

def test_correcting_the_description_updates_the_same_row(db, client, env):
    inv = _invoice(env)
    line = _line(inv)
    status, body = _put(client, f"/api/v1/invoices/{inv.id}/lines/{line.id}",
                        _token(client),
                        {"part_description": "Oil Filter (genuine)",
                         "part_number": "TOY-OF-002", "uom": "PCS",
                         "specification": "For 1NZ-FE"})
    assert status == 200, body
    assert body["line"]["id"] == line.id
    assert body["line"]["part_description"] == "Oil Filter (genuine)"
    assert body["line"]["part_number"] == "TOY-OF-002"
    rows = MaintenanceInvoiceLine.query.filter_by(invoice_id=inv.id).all()
    assert len(rows) == 1, "an edit must not leave a second line behind"


def test_fields_not_sent_are_left_alone(db, client, env):
    inv = _invoice(env)
    line = _line(inv, part_number="TOY-OF-001", uom="PCS")
    _put(client, f"/api/v1/invoices/{inv.id}/lines/{line.id}",
         _token(client), {"quantity": "3"})
    row = _db.session.get(MaintenanceInvoiceLine, line.id)
    assert row.part_number == "TOY-OF-001"
    assert row.uom == "PCS"
    assert row.part_description == "Oil Filter"


# ── Test E: invalid input is refused, nothing is written ────────────────────

@pytest.mark.parametrize("payload, field", [
    ({"quantity": "abc"}, "quantity"),
    ({"unit_cost": "1,200"}, "unit_cost"),
    ({"discount": "x"}, "discount"),
])
def test_a_value_that_is_not_a_number_is_refused(db, client, env,
                                                 payload, field):
    inv = _invoice(env)
    line = _line(inv)
    status, body = _put(client, f"/api/v1/invoices/{inv.id}/lines/{line.id}",
                        _token(client), payload)
    assert status == 400, body
    assert field in body.get("fields", {})
    row = _db.session.get(MaintenanceInvoiceLine, line.id)
    assert row.line_amount == Decimal("1800.00"), "the saved line must stand"


def test_a_required_field_cannot_be_blanked(db, client, env):
    inv = _invoice(env)
    line = _line(inv)
    for payload in ({"part_description": ""}, {"expense_category": ""},
                    {"charged_to": ""}):
        status, _ = _put(client, f"/api/v1/invoices/{inv.id}/lines/{line.id}",
                         _token(client), payload)
        assert status == 400
    row = _db.session.get(MaintenanceInvoiceLine, line.id)
    assert row.part_description == "Oil Filter"
    assert row.expense_category == "PARTS"


def test_a_negative_quantity_is_refused(db, client, env):
    inv = _invoice(env)
    line = _line(inv)
    status, body = _put(client, f"/api/v1/invoices/{inv.id}/lines/{line.id}",
                        _token(client), {"quantity": "-1"})
    assert status == 400, body
    assert _db.session.get(
        MaintenanceInvoiceLine, line.id).quantity == Decimal("4")


# ── The URL's invoice id is enforced ────────────────────────────────────────

def test_a_line_cannot_be_edited_through_another_invoices_url(
        db, client, env):
    mine = _invoice(env)
    other = MaintenanceInvoiceService().create(
        maintenance_order_id=env["order"].id, vendor_id=env["vendor"].id,
        invoice_number="INV-OTHER", invoice_date=date.today(),
        vat_type="VAT_EXCLUSIVE", vat_percentage=12, user=env["clerk"])
    line = _line(mine)
    status, _ = _put(client, f"/api/v1/invoices/{other.id}/lines/{line.id}",
                     _token(client), {"quantity": "99"})
    assert status == 404
    assert _db.session.get(
        MaintenanceInvoiceLine, line.id).quantity == Decimal("4")


def test_a_line_that_does_not_exist_is_a_404(db, client, env):
    inv = _invoice(env)
    status, _ = _put(client, f"/api/v1/invoices/{inv.id}/lines/999999",
                     _token(client), {"quantity": "1"})
    assert status == 404


# ── Test G: permissions and locks ───────────────────────────────────────────

def test_editing_needs_the_update_permission(db, client, env):
    inv = _invoice(env)
    line = _line(inv)
    status, _ = _put(client, f"/api/v1/invoices/{inv.id}/lines/{line.id}",
                     _token(client, "linereader"), {"quantity": "3"})
    assert status == 403
    assert _db.session.get(
        MaintenanceInvoiceLine, line.id).quantity == Decimal("4")


def test_anonymous_cannot_edit(db, client, env):
    inv = _invoice(env)
    line = _line(inv)
    r = client.put(f"/api/v1/invoices/{inv.id}/lines/{line.id}",
                   json={"quantity": "3"})
    assert r.status_code == 401


def test_an_approved_invoice_refuses_the_correction(db, client, env):
    """Same lock adding a line hits: reopen it first."""
    inv = _invoice(env)
    line = _line(inv)
    inv.status = "APPROVED"
    _db.session.commit()
    status, body = _put(client, f"/api/v1/invoices/{inv.id}/lines/{line.id}",
                        _token(client), {"quantity": "3"})
    assert status == 409, body
    assert "reopen" in json.dumps(body).lower()
    assert _db.session.get(
        MaintenanceInvoiceLine, line.id).quantity == Decimal("4")


def test_a_completed_order_refuses_the_correction(db, client, env):
    inv = _invoice(env)
    line = _line(inv)
    env["order"].status = "COMPLETED"
    _db.session.commit()
    status, _ = _put(client, f"/api/v1/invoices/{inv.id}/lines/{line.id}",
                     _token(client), {"quantity": "3"})
    assert status == 409
    assert _db.session.get(
        MaintenanceInvoiceLine, line.id).quantity == Decimal("4")


# ── Test: the financial change is on the record ─────────────────────────────

def test_the_correction_is_audit_logged_with_before_and_after(
        db, client, env):
    from app.core.models.audit_log import AuditLog

    inv = _invoice(env)
    line = _line(inv)
    _put(client, f"/api/v1/invoices/{inv.id}/lines/{line.id}",
         _token(client), {"quantity": "3"})

    entry = (AuditLog.query
             .filter_by(table_name="maintenance_invoice_lines",
                        record_id=line.id, action="UPDATE")
             .order_by(AuditLog.id.desc()).first())
    assert entry is not None, "a money change must leave a trail"
    assert str(entry.old_values.get("quantity")).startswith("4")
    assert str(entry.new_values.get("quantity")).startswith("3")
    assert entry.user_id == env["clerk"].id
