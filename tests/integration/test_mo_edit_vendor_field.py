"""The Maintenance Order Edit screen (returned-for-revision flow) was
missing the Vendor field.

The backend chain was already complete before this fix: the route
already forwarded `vendor_id` from the posted form straight into
`MaintenanceOrderService.update()` (which already allows `vendor_id`
whenever the order is fully editable -- a never-submitted Draft or one
an approver Returned), and the API/service layers already supported it.
The ONLY gap was the Jinja template itself: `maintenanceorder_edit.html`
rendered Scheduled Date, Odometer, Estimated Cost, Maintenance/
Transaction Type and Assigned Mechanic, but no Vendor control at all --
so an initiator whose request was returned because the WRONG VENDOR was
picked had no way to fix that specific thing and resubmit.

Written before the template fix: it fails on the missing field, passes
once the field (and its current-value preselection, matching the
existing Vendor-field pattern used by battery_form.html /
maintenanceorder_form.html) is added.
"""
from datetime import date

from app.core.approval.models import ApprovalInstance
from app.core.security.password import hash_password
from app.core.security.registry import sync_permissions
from app.modules.document_config.models import DocumentType
from app.modules.document_config.service import DocumentTypeService
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.reference.service import (
    VehicleTypeService, MaintenanceTypeService)
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.vendor.service import VendorService
from app.modules.transactions.maintenance_order.service import (
    MaintenanceOrderService)
from app.modules.user_management.models import Permission, Role, User


def _login(client, db, *, codes=()):
    role = Role(name="MOVendorEditRole")
    for code in codes:
        module, action = code.split(".")
        p = Permission.query.filter_by(code=code).first()
        if p is None:
            p = Permission(code=code, module=module, action=action)
            db.session.add(p)
        role.permissions.append(p)
    u = User(username="mo_vendor_edit_user", email="mo_vendor_edit@x.com",
             password_hash=hash_password("pw123456"))
    u.roles.append(role)
    db.session.add_all([role, u])
    db.session.commit()
    client.post("/login", data={"username": "mo_vendor_edit_user",
                                "password": "pw123456"})
    return u


def _returned_order(db, user, suffix):
    """A MO the initiator raised, approval-returned for revision --
    exactly the screen from the bug report (status 'Returned for
    Revision')."""
    sync_permissions()
    if DocumentType.query.filter_by(code="MO").first() is None:
        DocumentTypeService().create(code="MO", name="Maintenance Order",
                                     requires_approval=False,
                                     auto_numbering=True)

    branch = BranchService().create(code=f"BR-{suffix}", name="Vendor Branch")
    vt = VehicleTypeService().create(code=f"LV-{suffix}", name="Light",
                                     category="LIGHT")
    mt = MaintenanceTypeService().create(code=f"PM-{suffix}", name="PMS",
                                         category="PREVENTIVE")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Isuzu", model="Elf", year=2023,
        branch_id=branch.id, conduction_number=f"CN-{suffix}")
    wrong_vendor = VendorService().create(
        code=f"VEND-WRONG-{suffix}", name="Wrong Auto Shop")
    right_vendor = VendorService().create(
        code=f"VEND-RIGHT-{suffix}", name="Correct Auto Shop")

    order = MaintenanceOrderService().create(
        vehicle_id=vehicle.id, maintenance_type_id=mt.id,
        scheduled_date=date.today(), vendor_id=wrong_vendor.id,
        user=user)
    order.requested_by = user.id
    db.session.commit()

    instance = ApprovalInstance(
        document_type_id=DocumentType.query.filter_by(code="MO").first().id,
        reference_table="maintenance_orders", reference_id=order.id,
        status="RETURNED")
    db.session.add(instance)
    db.session.flush()
    order.approval_instance_id = instance.id
    db.session.commit()
    db.session.expire_all()
    return order, wrong_vendor, right_vendor


def test_edit_screen_shows_vendor_field_preselected_to_current_vendor(client, db):
    user = _login(client, db, codes=["maintenanceorder.view",
                                     "maintenanceorder.update"])
    order, wrong_vendor, _ = _returned_order(db, user, "A")

    resp = client.get(f"/transactions/maintenance-orders/{order.id}/edit")
    html = resp.get_data(as_text=True)

    assert resp.status_code == 200
    assert 'name="vendor_id"' in html, (
        "The Edit screen for a Returned order has no way to change the "
        "Vendor -- exactly the gap reported against MO-2026-000043.")
    assert wrong_vendor.name in html, (
        "The current (wrong) vendor should be shown pre-selected, so the "
        "initiator can see what needs correcting.")


def test_submitting_a_new_vendor_updates_the_order(client, db):
    user = _login(client, db, codes=["maintenanceorder.view",
                                     "maintenanceorder.update"])
    order, wrong_vendor, right_vendor = _returned_order(db, user, "B")

    resp = client.post(
        f"/transactions/maintenance-orders/{order.id}/edit",
        data={
            "scheduled_date": date.today().isoformat(),
            "vendor_id": str(right_vendor.id),
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200

    db.session.expire_all()
    from app.modules.transactions.maintenance_order.models import MaintenanceOrder
    refreshed = db.session.get(MaintenanceOrder, order.id)
    assert refreshed.vendor_id == right_vendor.id
