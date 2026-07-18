from datetime import date

import pytest

from app.core.security.password import hash_password
from app.modules.user_management.models import User, Role, Permission
from app.modules.master_data.vendor.service import VendorService
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.reference.service import (
    VehicleTypeService, MaintenanceTypeService)
from app.modules.master_data.org.service import BranchService
from app.modules.transactions.maintenance_order.service import MaintenanceOrderService
from app.modules.transactions.maintenance_invoice.service import (
    MaintenanceInvoiceService)
from app.modules.document_config.service import (
    DocumentTypeService, NumberingSchemeService)


def _login(client, db, *, codes=()):
    role = Role(name="MultiInvoicePrintRole")
    for code in codes:
        m, a = code.split(".")
        p = Permission.query.filter_by(code=code).first()
        if p is None:
            p = Permission(code=code, module=m, action=a)
            db.session.add(p)
        role.permissions.append(p)
    u = User(username="multi_invoice_print_user", email="multi_invoice_print_user@x.com",
             password_hash=hash_password("pw123456"))
    u.roles.append(role)
    db.session.add_all([role, u])
    db.session.commit()
    client.post("/login", data={"username": "multi_invoice_print_user", "password": "pw123456"})
    return u


def test_print_groups_line_items_by_invoice_number(client, db):
    """Reproduces exactly the reported use case: a Maintenance Order
    with parts from one supplier (e.g. CASA/dealer) and labor from a
    completely different supplier -- each invoice's items must be
    clearly grouped under its OWN invoice number and vendor, not merged
    into one undifferentiated table."""
    branch = BranchService().create(code="BR-MULTIINVPRINT", name="Multi Invoice Print Branch")
    vt = VehicleTypeService().create(code="LV-MULTIINVPRINT", name="Light", category="LIGHT")
    mt = MaintenanceTypeService().create(code="MULTIINVPRINT-MT", name="Multi Invoice Test",
                                         category="CM")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2024,
        branch_id=branch.id, conduction_number="MULTIINVPRINT-000")
    casa = VendorService().create(code="VEND-CASA", name="Toyota CASA Dealer",
                                  vendor_type="GOODS")
    labor_shop = VendorService().create(code="VEND-LABORSHOP", name="Independent Labor Shop",
                                        vendor_type="SERVICES")
    for code in ["MO", "INV"]:
        from app.modules.document_config.models import DocumentType
        if not DocumentType.query.filter_by(code=code).first():
            DocumentTypeService().create(code=code, name=code, requires_approval=False,
                                         auto_numbering=True)
            dt = DocumentType.query.filter_by(code=code).first()
            NumberingSchemeService().create(document_type_id=dt.id, prefix=code,
                                            include_year=True, digit_count=6,
                                            reset_policy="YEARLY")
    order = MaintenanceOrderService().create(
        vehicle_id=vehicle.id, maintenance_type_id=mt.id,
        scheduled_date=date.today(), user=None)
    order.status = "COMPLETED"
    order.completed_date = date.today()
    db.session.commit()

    inv1 = MaintenanceInvoiceService().create(
        maintenance_order_id=order.id, vendor_id=casa.id,
        invoice_number="CASA-INV-001", invoice_date=date.today(),
        vat_type="NON_VAT", vat_percentage=0, user=None)
    MaintenanceInvoiceService().add_line(
        inv1.id, part_description="OEM Brake Pads", quantity=1, unit_cost=5000,
        discount=0, expense_category="PARTS", charged_to="COMPANY")

    inv2 = MaintenanceInvoiceService().create(
        maintenance_order_id=order.id, vendor_id=labor_shop.id,
        invoice_number="LABORSHOP-INV-777", invoice_date=date.today(),
        vat_type="NON_VAT", vat_percentage=0, user=None)
    MaintenanceInvoiceService().add_line(
        inv2.id, part_description="Brake Job Labor", quantity=1, unit_cost=1200,
        discount=0, expense_category="LABOR", charged_to="COMPANY")

    _login(client, db, codes=["maintenanceorder.view", "maintenanceorder.print"])
    resp = client.get(f"/transactions/maintenance-orders/{order.id}/print")
    assert resp.status_code == 200
    html = resp.data.decode()

    # Each invoice must appear under its OWN clearly labeled section...
    assert "Invoice: CASA-INV-001 — Toyota CASA Dealer" in html
    assert "Invoice: LABORSHOP-INV-777 — Independent Labor Shop" in html
    # ...with the CASA section containing the brake pads and NOT the
    # independent shop's labor line (proving they're actually grouped,
    # not just both present anywhere on the page).
    casa_section_start = html.index("CASA-INV-001")
    laborshop_section_start = html.index("LABORSHOP-INV-777")
    casa_section = html[casa_section_start:laborshop_section_start]
    assert "OEM Brake Pads" in casa_section
    assert "Brake Job Labor" not in casa_section

    # And an overall combined summary still exists at the end.
    assert "Invoice Summary (All Invoices Combined)" in html
    assert "6,200.00" in html  # 5000 + 1200 total across all invoices
