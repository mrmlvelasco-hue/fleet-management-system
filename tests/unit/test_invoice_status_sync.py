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
    branch = BranchService().create(code="BR-INVSTATUS", name="Invoice Status Branch")
    vt = VehicleTypeService().create(code="LV-INVSTATUS", name="Light", category="LIGHT")
    mt = MaintenanceTypeService().create(code="INVSTATUS-MT", name="Invoice Status Test",
                                         category="CM")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2024,
        branch_id=branch.id, conduction_number="INVSTATUS-000")
    vendor = VendorService().create(code="VEND-INVSTATUS", name="ABC Motors",
                                    vendor_type="SERVICES")
    for code in ["MO", "INV"]:
        from app.modules.document_config.models import DocumentType
        if not DocumentType.query.filter_by(code=code).first():
            DocumentTypeService().create(code=code, name=code,
                                         requires_approval=False, auto_numbering=True)
            dt = DocumentType.query.filter_by(code=code).first()
            NumberingSchemeService().create(document_type_id=dt.id, prefix=code,
                                            include_year=True, digit_count=6,
                                            reset_policy="YEARLY")
    order = MaintenanceOrderService().create(
        vehicle_id=vehicle.id, maintenance_type_id=mt.id,
        scheduled_date=date.today(), user=None)
    order.status = "COMPLETED"
    from app.extensions import db as _db
    _db.session.commit()
    return branch, vehicle, vendor, order


def test_submit_without_approval_configured_marks_invoice_approved(db, env):
    """Reproduces the reported bug: when Invoice approval isn't
    configured (requires_approval=False), the underlying ApprovalInstance
    auto-approves itself immediately, but the invoice's OWN status field
    was never being synced -- it stayed stuck at DRAFT forever, with no
    way to reach a completed state without a separate manual action."""
    branch, vehicle, vendor, order = env
    from app.modules.user_management.models import User
    from app.core.security.password import hash_password
    requester = User(username="invstatus_requester", email="invstatus_requester@x.com",
                     password_hash=hash_password("pw123456"))
    db.session.add(requester)
    db.session.commit()

    inv = MaintenanceInvoiceService().create(
        maintenance_order_id=order.id, vendor_id=vendor.id,
        invoice_number="INV-STATUS-0001", invoice_date=date.today(),
        vat_type="NON_VAT", vat_percentage=0, user=requester)
    assert inv.status == "DRAFT"

    MaintenanceInvoiceService().submit(inv.id, user=requester)

    inv = MaintenanceInvoiceService().get_by_id(inv.id)
    assert inv.status == "APPROVED"
    assert inv.approval_instance.status == "APPROVED"


def test_submit_with_approval_configured_marks_invoice_submitted(db, env):
    """When approval IS configured, submitting should move the invoice
    to SUBMITTED (awaiting action), not silently jump straight to
    APPROVED before anyone has actually approved it."""
    branch, vehicle, vendor, order = env
    from app.modules.document_config.models import DocumentType
    dt = DocumentType.query.filter_by(code="INV").first()
    dt.requires_approval = True
    db.session.commit()

    from app.modules.approval_config.service import (
        ApprovalPathService, ApprovalMatrixService)
    from app.modules.user_management.models import Role
    role = Role(name="InvStatusApproverRole")
    db.session.add(role)
    db.session.commit()
    path = ApprovalPathService().create(name="Invoice Status Path", levels=[
        {"level_number": 1, "approver_type": "ROLE", "role_id": role.id}])
    ApprovalMatrixService().create(dt.id, path.id, min_amount=None, max_amount=None)

    inv = MaintenanceInvoiceService().create(
        maintenance_order_id=order.id, vendor_id=vendor.id,
        invoice_number="INV-STATUS-0002", invoice_date=date.today(),
        vat_type="NON_VAT", vat_percentage=0, user=None)
    from app.modules.user_management.models import User
    from app.core.security.password import hash_password
    requester2 = User(username="invstatus_requester2", email="invstatus_requester2@x.com",
                      password_hash=hash_password("pw123456"))
    db.session.add(requester2)
    db.session.commit()
    MaintenanceInvoiceService().submit(inv.id, user=requester2)

    inv = MaintenanceInvoiceService().get_by_id(inv.id)
    assert inv.status == "SUBMITTED"
    assert inv.status != "APPROVED"  # must NOT jump ahead of the real approval


def test_submit_with_no_matrix_configured_completes_instead_of_getting_stuck(
        db, env):
    """Reproduces the reported bug: Invoice approval IS marked as
    required, but no Approval Matrix has actually been configured for it
    (e.g. setup was skipped) -- this used to raise NoMatrixError straight
    out of submit(), leaving the invoice stuck in DRAFT with a confusing
    error and no way forward. There's nothing configured to route this
    to, so it should behave the same as "no approval required": go
    straight to APPROVED."""
    branch, vehicle, vendor, order = env
    from app.modules.document_config.models import DocumentType
    dt = DocumentType.query.filter_by(code="INV").first()
    dt.requires_approval = True
    db.session.commit()
    # Deliberately NOT creating an ApprovalMatrix -- this is the exact
    # misconfiguration being tested.

    inv = MaintenanceInvoiceService().create(
        maintenance_order_id=order.id, vendor_id=vendor.id,
        invoice_number="INV-STATUS-0003", invoice_date=date.today(),
        vat_type="NON_VAT", vat_percentage=0, user=None)
    from app.modules.user_management.models import User
    from app.core.security.password import hash_password
    requester3 = User(username="invstatus_requester3",
                      email="invstatus_requester3@x.com",
                      password_hash=hash_password("pw123456"))
    db.session.add(requester3)
    db.session.commit()

    # Must NOT raise NoMatrixError.
    result = MaintenanceInvoiceService().submit(inv.id, user=requester3)
    assert result.status == "APPROVED"

    inv = MaintenanceInvoiceService().get_by_id(inv.id)
    assert inv.status == "APPROVED"
