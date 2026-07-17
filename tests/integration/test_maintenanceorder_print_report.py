from datetime import date

import pytest

from app.core.security.password import hash_password
from app.modules.user_management.models import User, Role, Permission
from app.modules.master_data.vendor.service import VendorService
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.driver.service import DriverService
from app.modules.master_data.reference.service import (
    VehicleTypeService, MaintenanceTypeService)
from app.modules.master_data.org.service import BranchService
from app.modules.transactions.maintenance_order.service import MaintenanceOrderService
from app.modules.transactions.maintenance_invoice.service import (
    MaintenanceInvoiceService)
from app.modules.document_config.service import (
    DocumentTypeService, NumberingSchemeService)


def _login(client, db, *, codes=()):
    role = Role(name="MOPrintRole")
    for code in codes:
        m, a = code.split(".")
        p = Permission.query.filter_by(code=code).first()
        if p is None:
            p = Permission(code=code, module=m, action=a)
            db.session.add(p)
        role.permissions.append(p)
    u = User(username="mo_print_user", email="mo_print_user@x.com",
             password_hash=hash_password("pw123456"))
    u.roles.append(role)
    db.session.add_all([role, u])
    db.session.commit()
    client.post("/login", data={"username": "mo_print_user", "password": "pw123456"})
    return u


@pytest.fixture()
def env(db):
    branch = BranchService().create(code="BR-MOPRINT", name="MO Print Branch")
    vt = VehicleTypeService().create(code="LV-MOPRINT", name="Light", category="LIGHT")
    mt = MaintenanceTypeService().create(code="MOPRINT-MT", name="Print Test MT",
                                         category="CM")
    driver = DriverService().create(
        employee_number="EMP-MOPRINT1", first_name="Maria", last_name="Santos",
        license_number="LIC-MOPRINT1", license_expiry=date(2030, 1, 1),
        license_type="PROFESSIONAL", branch_id=branch.id,
        job_title="Regional Sales Manager")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Ford", model="Escape", year=2024,
        branch_id=branch.id, conduction_number="MOPRINT-000",
        assigned_driver_id=driver.id)
    vendor = VendorService().create(code="VEND-MOPRINT", name="Print Vendor",
                                    vendor_type="SERVICES")
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
        scheduled_date=date.today(), description="Replace brake pads", user=None)
    order.status = "COMPLETED"
    order.completed_date = date.today()
    from app.extensions import db as _db
    _db.session.commit()
    return branch, vehicle, driver, vendor, order


def test_print_shows_pm_parameter_mapping_fields(client, db, env):
    """PM2-PM7 from the spec: Brand, Model, Plate, Current Assignee,
    Branch, Assignee Position."""
    branch, vehicle, driver, vendor, order = env
    _login(client, db, codes=["maintenanceorder.view", "maintenanceorder.print"])
    resp = client.get(f"/transactions/maintenance-orders/{order.id}/print")
    assert resp.status_code == 200
    assert b"Ford" in resp.data
    assert b"Escape" in resp.data
    assert b"MOPRINT-000" in resp.data
    assert b"Maria Santos" in resp.data
    assert b"Regional Sales Manager" in resp.data
    assert b"MO Print Branch" in resp.data


def test_print_shows_last_completed_work_order(client, db, env):
    branch, vehicle, driver, vendor, order = env
    _login(client, db, codes=["maintenanceorder.view", "maintenanceorder.print"])
    # A prior completed order for the same vehicle
    prior = MaintenanceOrderService().create(
        vehicle_id=vehicle.id, maintenance_type_id=order.maintenance_type_id,
        scheduled_date=date(2026, 1, 1), user=None)
    prior.status = "COMPLETED"
    prior.completed_date = date(2026, 1, 5)
    db.session.commit()

    resp = client.get(f"/transactions/maintenance-orders/{order.id}/print")
    assert resp.status_code == 200
    assert prior.document_number.encode() in resp.data


def test_print_shows_parts_labor_and_invoice_summary(client, db, env):
    branch, vehicle, driver, vendor, order = env
    _login(client, db, codes=["maintenanceorder.view", "maintenanceorder.print"])
    inv = MaintenanceInvoiceService().create(
        maintenance_order_id=order.id, vendor_id=vendor.id,
        invoice_number="INV-PRINT-1", invoice_date=date.today(),
        vat_type="NON_VAT", vat_percentage=0, user=None)
    MaintenanceInvoiceService().add_line(
        inv.id, part_description="Brake Pads", quantity=2, unit_cost=500,
        discount=0, expense_category="PARTS", charged_to="COMPANY")
    MaintenanceInvoiceService().add_line(
        inv.id, part_description="Labor - Brake Job", quantity=1, unit_cost=800,
        discount=0, expense_category="LABOR", charged_to="COMPANY")

    resp = client.get(f"/transactions/maintenance-orders/{order.id}/print")
    assert resp.status_code == 200
    assert b"Brake Pads" in resp.data
    assert b"Labor - Brake Job" in resp.data
    assert b"Invoice Summary" in resp.data
    assert b"1,800.00" in resp.data  # total invoice amount


def test_print_shows_work_description_and_remarks_section(client, db, env):
    branch, vehicle, driver, vendor, order = env
    _login(client, db, codes=["maintenanceorder.view", "maintenanceorder.print"])
    resp = client.get(f"/transactions/maintenance-orders/{order.id}/print")
    assert resp.status_code == 200
    assert b"Replace brake pads" in resp.data
    assert b"Remarks" in resp.data


def test_print_shows_requester_signature(client, db, env):
    branch, vehicle, driver, vendor, order = env
    requester = _login(client, db, codes=["maintenanceorder.view",
                                          "maintenanceorder.print"])
    order.requested_by = requester.id
    db.session.commit()
    resp = client.get(f"/transactions/maintenance-orders/{order.id}/print")
    assert resp.status_code == 200
    assert b"Requested By" in resp.data
    assert requester.full_name.encode() in resp.data


def test_print_shows_draft_watermark(client, db, env):
    branch, vehicle, driver, vendor, order = env
    order.status = "DRAFT"
    db.session.commit()
    _login(client, db, codes=["maintenanceorder.view", "maintenanceorder.print"])
    resp = client.get(f"/transactions/maintenance-orders/{order.id}/print")
    assert resp.status_code == 200
    assert b"DRAFT</div>" in resp.data


def test_print_includes_qr_code_script(client, db, env):
    branch, vehicle, driver, vendor, order = env
    _login(client, db, codes=["maintenanceorder.view", "maintenanceorder.print"])
    resp = client.get(f"/transactions/maintenance-orders/{order.id}/print")
    assert resp.status_code == 200
    assert b"qrCanvas" in resp.data
