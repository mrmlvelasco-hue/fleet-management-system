from datetime import date

import pytest

from app.modules.transactions.maintenance_invoice.service import (
    MaintenanceInvoiceService)
from app.modules.master_data.vendor.service import VendorService
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.reference.service import (
    VehicleTypeService, MaintenanceTypeService)
from app.modules.master_data.org.service import BranchService
from app.modules.transactions.maintenance_order.service import MaintenanceOrderService
from app.modules.document_config.service import (
    DocumentTypeService, NumberingSchemeService)


@pytest.fixture()
def env(db):
    branch = BranchService().create(code="BR-INV", name="Invoice Test Branch")
    vt = VehicleTypeService().create(code="LV-INV", name="Light", category="LIGHT")
    mt = MaintenanceTypeService().create(code="INV-MT", name="Invoice Test MT",
                                         category="CM")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2024,
        branch_id=branch.id, conduction_number="INV-000")
    vendor = VendorService().create(code="VEND-INV1", name="ABC Motors",
                                    vendor_type="SERVICES", email="abc@vendor.com",
                                    contact_person="Juan Dela Cruz", phone="0917-000-0000")
    for code in ["MO", "INV"]:
        DocumentTypeService().create(code=code, name=code,
                                     requires_approval=False, auto_numbering=True)
        from app.modules.document_config.models import DocumentType
        dt = DocumentType.query.filter_by(code=code).first()
        NumberingSchemeService().create(document_type_id=dt.id, prefix=code,
                                        include_year=True, digit_count=6,
                                        reset_policy="YEARLY")
    order = MaintenanceOrderService().create(
        vehicle_id=vehicle.id, maintenance_type_id=mt.id,
        scheduled_date=date.today(), user=None)
    order.status = "COMPLETED"
    order.completed_date = date.today()
    from app.extensions import db
    db.session.commit()
    return branch, vehicle, vendor, order


def test_create_invoice_header(db, env):
    branch, vehicle, vendor, order = env
    inv = MaintenanceInvoiceService().create(
        maintenance_order_id=order.id, vendor_id=vendor.id,
        invoice_number="INV-2026-0001", invoice_date=date.today(),
        vat_type="VAT_EXCLUSIVE", vat_percentage=12, user=None)
    assert inv.maintenance_order_id == order.id
    assert inv.vendor_id == vendor.id
    assert inv.status == "DRAFT"
    assert inv.document_number  # auto-numbered


def test_vat_exclusive_line_calculation(db, env):
    """VAT Exclusive: unit cost is pre-VAT; VAT is added on top."""
    branch, vehicle, vendor, order = env
    inv = MaintenanceInvoiceService().create(
        maintenance_order_id=order.id, vendor_id=vendor.id,
        invoice_number="INV-2026-0002", invoice_date=date.today(),
        vat_type="VAT_EXCLUSIVE", vat_percentage=12, user=None)
    line = MaintenanceInvoiceService().add_line(
        inv.id, part_description="Brake Pads", uom="SET", quantity=2,
        unit_cost=1000, discount=0, expense_category="PARTS",
        charged_to="COMPANY")
    # line_amount = 2*1000 = 2000; vat = 2000*0.12 = 240; total = 2240
    assert float(line.line_amount) == 2000.0
    assert float(line.vat_amount) == 240.0
    assert float(line.total_amount) == 2240.0

    inv = MaintenanceInvoiceService().get_by_id(inv.id)
    assert float(inv.total_invoice_amount) == 2240.0
    assert float(inv.total_parts_cost) == 2000.0
    assert float(inv.total_vat) == 240.0


def test_vat_inclusive_line_calculation(db, env):
    """VAT Inclusive: unit cost already includes VAT; we back it out."""
    branch, vehicle, vendor, order = env
    inv = MaintenanceInvoiceService().create(
        maintenance_order_id=order.id, vendor_id=vendor.id,
        invoice_number="INV-2026-0003", invoice_date=date.today(),
        vat_type="VAT_INCLUSIVE", vat_percentage=12, user=None)
    line = MaintenanceInvoiceService().add_line(
        inv.id, part_description="Oil Change", uom="SERVICE", quantity=1,
        unit_cost=2240, discount=0, expense_category="LABOR",
        charged_to="COMPANY")
    # total_amount = 2240 (already inclusive); line_amount (net) = 2240/1.12 = 2000; vat = 240
    assert round(float(line.line_amount), 2) == 2000.0
    assert round(float(line.vat_amount), 2) == 240.0
    assert float(line.total_amount) == 2240.0


def test_non_vat_line_calculation(db, env):
    branch, vehicle, vendor, order = env
    inv = MaintenanceInvoiceService().create(
        maintenance_order_id=order.id, vendor_id=vendor.id,
        invoice_number="INV-2026-0004", invoice_date=date.today(),
        vat_type="NON_VAT", vat_percentage=0, user=None)
    line = MaintenanceInvoiceService().add_line(
        inv.id, part_description="Towing Fee", uom="SERVICE", quantity=1,
        unit_cost=1500, discount=0, expense_category="TOWING",
        charged_to="COMPANY")
    assert float(line.vat_amount) == 0.0
    assert float(line.line_amount) == 1500.0
    assert float(line.total_amount) == 1500.0


def test_discount_reduces_line_amount(db, env):
    branch, vehicle, vendor, order = env
    inv = MaintenanceInvoiceService().create(
        maintenance_order_id=order.id, vendor_id=vendor.id,
        invoice_number="INV-2026-0005", invoice_date=date.today(),
        vat_type="VAT_EXCLUSIVE", vat_percentage=12, user=None)
    line = MaintenanceInvoiceService().add_line(
        inv.id, part_description="Tires", uom="SET", quantity=4,
        unit_cost=3000, discount=1000, expense_category="TIRES",
        charged_to="COMPANY")
    # (4*3000) - 1000 = 11000; vat = 1320; total = 12320
    assert float(line.line_amount) == 11000.0
    assert float(line.vat_amount) == 1320.0
    assert float(line.total_amount) == 12320.0


def test_summary_recalculates_across_multiple_lines(db, env):
    branch, vehicle, vendor, order = env
    inv = MaintenanceInvoiceService().create(
        maintenance_order_id=order.id, vendor_id=vendor.id,
        invoice_number="INV-2026-0006", invoice_date=date.today(),
        vat_type="VAT_EXCLUSIVE", vat_percentage=12, user=None)
    MaintenanceInvoiceService().add_line(
        inv.id, part_description="Parts A", uom="PC", quantity=1,
        unit_cost=1000, discount=0, expense_category="PARTS", charged_to="COMPANY")
    MaintenanceInvoiceService().add_line(
        inv.id, part_description="Labor A", uom="HR", quantity=2,
        unit_cost=500, discount=0, expense_category="LABOR", charged_to="COMPANY")

    inv = MaintenanceInvoiceService().get_by_id(inv.id)
    assert float(inv.total_parts_cost) == 1000.0
    assert float(inv.total_labor_cost) == 1000.0
    assert float(inv.total_vat) == 240.0  # (1000+1000)*0.12
    assert float(inv.total_invoice_amount) == 2240.0
    assert len(inv.line_items) == 2


def test_removing_a_line_recalculates_summary(db, env):
    branch, vehicle, vendor, order = env
    inv = MaintenanceInvoiceService().create(
        maintenance_order_id=order.id, vendor_id=vendor.id,
        invoice_number="INV-2026-0007", invoice_date=date.today(),
        vat_type="NON_VAT", vat_percentage=0, user=None)
    line1 = MaintenanceInvoiceService().add_line(
        inv.id, part_description="A", uom="PC", quantity=1, unit_cost=500,
        discount=0, expense_category="PARTS", charged_to="COMPANY")
    MaintenanceInvoiceService().add_line(
        inv.id, part_description="B", uom="PC", quantity=1, unit_cost=300,
        discount=0, expense_category="PARTS", charged_to="COMPANY")

    MaintenanceInvoiceService().remove_line(line1.id)
    inv = MaintenanceInvoiceService().get_by_id(inv.id)
    assert float(inv.total_invoice_amount) == 300.0
    assert len(inv.line_items) == 1


def test_vendor_details_available_for_display(db, env):
    branch, vehicle, vendor, order = env
    inv = MaintenanceInvoiceService().create(
        maintenance_order_id=order.id, vendor_id=vendor.id,
        invoice_number="INV-2026-0008", invoice_date=date.today(),
        vat_type="NON_VAT", vat_percentage=0, user=None)
    assert inv.vendor.name == "ABC Motors"
    assert inv.vendor.contact_person == "Juan Dela Cruz"
    assert inv.vendor.email == "abc@vendor.com"


def test_multiple_invoices_per_maintenance_order(db, env):
    """Per the enhancement doc: 'Each Maintenance Order may have one or
    multiple invoices' -- e.g. parts from one supplier, labor from
    another."""
    branch, vehicle, vendor, order = env
    vendor2 = VendorService().create(code="VEND-INV2", name="XYZ Labor Shop",
                                     vendor_type="SERVICES")
    inv1 = MaintenanceInvoiceService().create(
        maintenance_order_id=order.id, vendor_id=vendor.id,
        invoice_number="INV-2026-0009", invoice_date=date.today(),
        vat_type="NON_VAT", vat_percentage=0, user=None)
    inv2 = MaintenanceInvoiceService().create(
        maintenance_order_id=order.id, vendor_id=vendor2.id,
        invoice_number="INV-2026-0010", invoice_date=date.today(),
        vat_type="NON_VAT", vat_percentage=0, user=None)

    invoices = MaintenanceInvoiceService().list_for_order(order.id)
    assert len(invoices) == 2
    assert {i.vendor_id for i in invoices} == {vendor.id, vendor2.id}
