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
from app.modules.document_config.service import (
    DocumentTypeService, NumberingSchemeService)


def _login(client, db, *, codes=()):
    role = Role(name="InvoiceUIRole")
    for code in codes:
        m, a = code.split(".")
        p = Permission.query.filter_by(code=code).first()
        if p is None:
            p = Permission(code=code, module=m, action=a)
            db.session.add(p)
        role.permissions.append(p)
    u = User(username="invoice_ui_user", email="invoice_ui_user@x.com",
             password_hash=hash_password("pw123456"))
    u.roles.append(role)
    db.session.add_all([role, u])
    db.session.commit()
    client.post("/login", data={"username": "invoice_ui_user", "password": "pw123456"})
    return u


@pytest.fixture()
def env(db):
    branch = BranchService().create(code="BR-INVUI", name="Invoice UI Branch")
    vt = VehicleTypeService().create(code="LV-INVUI", name="Light", category="LIGHT")
    mt = MaintenanceTypeService().create(code="INVUI-MT", name="Invoice UI MT",
                                         category="CM")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2024,
        branch_id=branch.id, conduction_number="INVUI-000")
    vendor = VendorService().create(code="VEND-INVUI", name="ABC Motors",
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
        scheduled_date=date.today(), user=None)
    order.status = "COMPLETED"
    order.completed_date = date.today()
    from app.extensions import db as _db
    _db.session.commit()
    return branch, vehicle, vendor, order


def test_mo_detail_shows_invoices_section_with_new_button(client, db, env):
    branch, vehicle, vendor, order = env
    _login(client, db, codes=["maintenanceorder.view", "maintenanceinvoice.create"])
    resp = client.get(f"/transactions/maintenance-orders/{order.id}")
    assert resp.status_code == 200
    assert b"Invoices &amp; Actual Expense" in resp.data or b"Invoices & Actual Expense" in resp.data
    assert f"/transactions/maintenance-orders/{order.id}/invoices/new".encode() in resp.data


def test_create_invoice_and_add_line_end_to_end(client, db, env):
    branch, vehicle, vendor, order = env
    _login(client, db, codes=["maintenanceorder.view", "maintenanceinvoice.view",
                              "maintenanceinvoice.create", "maintenanceinvoice.update"])

    resp = client.post(f"/transactions/maintenance-orders/{order.id}/invoices/new", data={
        "vendor_id": str(vendor.id), "invoice_number": "INV-UI-0001",
        "invoice_date": date.today().isoformat(), "vat_type": "VAT_EXCLUSIVE",
        "vat_percentage": "12",
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert b"Add line items below" in resp.data or b"Invoice Line Items" in resp.data

    from app.modules.transactions.maintenance_invoice.models import MaintenanceInvoice
    inv = MaintenanceInvoice.query.filter_by(invoice_number="INV-UI-0001").first()
    assert inv is not None

    resp2 = client.post(f"/transactions/invoices/{inv.id}/lines", data={
        "part_description": "Brake Pads", "specification": "Front",
        "uom": "SET", "quantity": "2", "unit_cost": "1000", "discount": "0",
        "expense_category": "PARTS", "charged_to": "COMPANY",
    }, follow_redirects=True)
    assert resp2.status_code == 200
    assert b"Brake Pads" in resp2.data
    assert b"2,240.00" in resp2.data  # total incl. 12% VAT


def test_invoice_shows_up_on_mo_detail_after_creation(client, db, env):
    branch, vehicle, vendor, order = env
    _login(client, db, codes=["maintenanceorder.view", "maintenanceinvoice.view",
                              "maintenanceinvoice.create"])
    client.post(f"/transactions/maintenance-orders/{order.id}/invoices/new", data={
        "vendor_id": str(vendor.id), "invoice_number": "INV-UI-0002",
        "invoice_date": date.today().isoformat(), "vat_type": "NON_VAT",
        "vat_percentage": "0",
    })
    resp = client.get(f"/transactions/maintenance-orders/{order.id}")
    assert resp.status_code == 200
    assert b"INV-UI-0002" in resp.data
    assert b"ABC Motors" in resp.data


def test_full_submit_approve_flow(client, db, env):
    branch, vehicle, vendor, order = env
    role = Role(name="InvApproverRole")
    for code in ["maintenanceinvoice.view", "maintenanceinvoice.create",
                "maintenanceinvoice.update"]:
        m, a = code.split(".")
        p = Permission.query.filter_by(code=code).first()
        if p is None:
            p = Permission(code=code, module=m, action=a)
            db.session.add(p)
        role.permissions.append(p)
    approver = User(username="inv_approver", email="inv_approver@x.com",
                    password_hash=hash_password("pw123456"))
    approver.roles.append(role)
    db.session.add_all([role, approver])
    db.session.commit()

    from app.modules.approval_config.service import (
        ApprovalPathService, ApprovalMatrixService)
    from app.modules.document_config.models import DocumentType
    dt = DocumentType.query.filter_by(code="INV").first()
    dt.requires_approval = True
    db.session.commit()
    path = ApprovalPathService().create(name="Invoice Path", levels=[
        {"level_number": 1, "approver_type": "ROLE", "role_id": role.id}])
    ApprovalMatrixService().create(dt.id, path.id, min_amount=None, max_amount=None)

    _login(client, db, codes=["maintenanceorder.view", "maintenanceinvoice.view",
                              "maintenanceinvoice.create", "maintenanceinvoice.update"])
    client.post(f"/transactions/maintenance-orders/{order.id}/invoices/new", data={
        "vendor_id": str(vendor.id), "invoice_number": "INV-UI-0003",
        "invoice_date": date.today().isoformat(), "vat_type": "NON_VAT",
        "vat_percentage": "0",
    })
    from app.modules.transactions.maintenance_invoice.models import MaintenanceInvoice
    inv = MaintenanceInvoice.query.filter_by(invoice_number="INV-UI-0003").first()

    resp = client.post(f"/transactions/invoices/{inv.id}/submit", follow_redirects=True)
    assert resp.status_code == 200
    assert b"submitted" in resp.data.lower()

    client.get("/logout")
    client.post("/login", data={"username": "inv_approver", "password": "pw123456"})
    resp2 = client.post(f"/transactions/invoices/{inv.id}/approve", follow_redirects=True)
    assert resp2.status_code == 200

    inv = MaintenanceInvoice.query.get(inv.id)
    assert inv.status == "APPROVED"


def test_cannot_edit_lines_on_approved_invoice(client, db, env):
    branch, vehicle, vendor, order = env
    _login(client, db, codes=["maintenanceorder.view", "maintenanceinvoice.view",
                              "maintenanceinvoice.create", "maintenanceinvoice.update"])
    client.post(f"/transactions/maintenance-orders/{order.id}/invoices/new", data={
        "vendor_id": str(vendor.id), "invoice_number": "INV-UI-0004",
        "invoice_date": date.today().isoformat(), "vat_type": "NON_VAT",
        "vat_percentage": "0",
    })
    from app.modules.transactions.maintenance_invoice.models import MaintenanceInvoice
    inv = MaintenanceInvoice.query.filter_by(invoice_number="INV-UI-0004").first()
    inv.status = "APPROVED"
    db.session.commit()

    resp = client.post(f"/transactions/invoices/{inv.id}/lines", data={
        "part_description": "Should Fail", "quantity": "1", "unit_cost": "100",
        "discount": "0", "expense_category": "PARTS", "charged_to": "COMPANY",
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert b"locked" in resp.data.lower() or b"reopen" in resp.data.lower()
    assert MaintenanceInvoice.query.get(inv.id).line_items == []
