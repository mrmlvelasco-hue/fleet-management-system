"""Invoice line item encoding without page reloads.

Reported: adding an item reloaded the page, lost keyboard focus, and
on an invoice with several items forced the person to scroll back down
to the form for every single line.

The server remains the single source of truth for every amount -- VAT,
discount and all totals are calculated in the service and merely
displayed by the browser. There is deliberately no second copy of the
money rules in JavaScript.
"""
from datetime import date

import pytest

from app.core.security.registry import sync_permissions
from app.cli import _seed_admin


@pytest.fixture()
def invoice(app, db):
    from app.modules.master_data.org.service import BranchService
    from app.modules.master_data.reference.service import VehicleTypeService
    from app.modules.master_data.vehicle.service import VehicleService
    from app.modules.master_data.vendor.service import VendorService
    from app.modules.transactions.maintenance_order.models import (
        MaintenanceOrder)
    from app.modules.transactions.maintenance_invoice.service import (
        MaintenanceInvoiceService)
    from app.modules.user_management.models import User

    sync_permissions(); db.session.commit()
    _seed_admin("Testpass123!")
    admin = User.query.filter_by(username="admin").first()

    branch = BranchService().create(code="BR-INV", name="Branch INV")
    vt = VehicleTypeService().create(code="LV-INV", name="Light",
                                     category="LIGHT")
    v = VehicleService().create(vehicle_type_id=vt.id, brand="Toyota",
                                model="Vios", year=2022, branch_id=branch.id,
                                conduction_number="INV-1")
    mo = MaintenanceOrder(vehicle_id=v.id, order_category="MAINTENANCE",
                          scheduled_date=date.today(), status="DRAFT")
    db.session.add(mo)
    db.session.commit()
    vendor = VendorService().create(code="V-INV", name="Invoice Vendor")
    inv = MaintenanceInvoiceService().create(
        maintenance_order_id=mo.id, vendor_id=vendor.id,
        invoice_number="INV-UX-1", invoice_date=date.today(), user=admin)
    db.session.commit()
    return inv


def _client(app):
    c = app.test_client()
    c.post("/login", data={"username": "admin", "password": "Testpass123!"},
          follow_redirects=True)
    return c


AJAX = {"X-Requested-With": "XMLHttpRequest"}


def _line_payload(**over):
    data = {"part_description": "Brake Pad", "quantity": "2",
            "unit_cost": "50.00", "discount": "0",
            "expense_category": "PARTS", "charged_to": "COMPANY_EXPENSE"}
    data.update(over)
    return data


def test_ajax_add_returns_json_not_a_redirect(app, db, invoice):
    r = _client(app).post(f"/transactions/invoices/{invoice.id}/lines",
                          data=_line_payload(), headers=AJAX)
    assert r.status_code == 200
    assert r.get_json()["ok"] is True


def test_ajax_add_returns_the_created_line_for_display(app, db, invoice):
    r = _client(app).post(f"/transactions/invoices/{invoice.id}/lines",
                          data=_line_payload(), headers=AJAX)
    line = r.get_json()["line"]
    assert line["part_description"] == "Brake Pad"
    assert line["id"]
    # Amounts come from the server already formatted -- the browser
    # never calculates money.
    assert line["line_amount"] == "100.00"


def test_ajax_add_returns_recalculated_invoice_totals(app, db, invoice):
    r = _client(app).post(f"/transactions/invoices/{invoice.id}/lines",
                          data=_line_payload(), headers=AJAX)
    totals = r.get_json()["totals"]
    assert totals["total_parts_cost"] == "100.00"
    assert "total_invoice_amount" in totals


def test_several_lines_accumulate_correctly(app, db, invoice):
    """Encoding a multi-line invoice is the actual reported scenario."""
    client = _client(app)
    for i in range(5):
        r = client.post(f"/transactions/invoices/{invoice.id}/lines",
                        data=_line_payload(part_description=f"Part {i}",
                                          quantity="1", unit_cost="10.00"),
                        headers=AJAX)
        assert r.get_json()["ok"] is True
    from app.modules.transactions.maintenance_invoice.models import (
        MaintenanceInvoice)
    inv = MaintenanceInvoice.query.get(invoice.id)
    assert len(inv.line_items) == 5
    assert float(inv.total_parts_cost) == 50.00


def test_ajax_delete_returns_updated_totals(app, db, invoice):
    client = _client(app)
    add = client.post(f"/transactions/invoices/{invoice.id}/lines",
                      data=_line_payload(), headers=AJAX)
    line_id = add.get_json()["line"]["id"]
    r = client.post(
        f"/transactions/invoices/{invoice.id}/lines/{line_id}/delete",
        headers=AJAX)
    assert r.status_code == 200
    assert r.get_json()["totals"]["total_parts_cost"] == "0.00"


def test_non_ajax_post_still_redirects(app, db, invoice):
    """Progressive enhancement: without JavaScript the original
    full-page behaviour must still work rather than dumping raw JSON on
    the person."""
    r = _client(app).post(f"/transactions/invoices/{invoice.id}/lines",
                          data=_line_payload())
    assert r.status_code == 302
    assert f"/invoices/{invoice.id}" in r.location


def test_bad_input_returns_a_usable_error_not_a_500(app, db, invoice):
    """A typo in a number must come back as a message beside the form,
    not a crash that loses everything already typed."""
    r = _client(app).post(f"/transactions/invoices/{invoice.id}/lines",
                          data=_line_payload(unit_cost="not-a-number"),
                          headers=AJAX)
    assert r.status_code == 400
    assert r.get_json()["ok"] is False
    assert r.get_json()["error"]


def test_page_includes_the_live_entry_script_and_hooks(app, db, invoice):
    html = _client(app).get(
        f"/transactions/invoices/{invoice.id}").get_data(as_text=True)
    assert 'id="invLineForm"' in html
    assert 'id="invLineBody"' in html
    assert 'id="invFirstField"' in html
    assert 'id="tot_total_invoice_amount"' in html
