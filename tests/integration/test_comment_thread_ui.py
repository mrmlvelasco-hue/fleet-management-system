from datetime import date

from app.core.security.password import hash_password
from app.modules.user_management.models import User, Role, Permission
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.org.service import BranchService
from app.modules.document_config.service import (
    DocumentTypeService, NumberingSchemeService)


def _login(client, db, *, codes=()):
    role = Role(name="CommentThreadRole")
    for code in codes:
        m, a = code.split(".")
        p = Permission.query.filter_by(code=code).first()
        if p is None:
            p = Permission(code=code, module=m, action=a)
            db.session.add(p)
        role.permissions.append(p)
    u = User(username="comment_ui_user", email="comment_ui_user@x.com",
             password_hash=hash_password("pw123456"))
    u.roles.append(role)
    db.session.add_all([role, u])
    db.session.commit()
    client.post("/login", data={"username": "comment_ui_user", "password": "pw123456"})
    return u


def _make_maintenance_order(db, branch, vt):
    from app.modules.master_data.reference.service import MaintenanceTypeService
    from app.modules.transactions.maintenance_order.service import MaintenanceOrderService
    mt = MaintenanceTypeService().create(code="CMTUI-5K", name="5K PMS",
                                         category="PREVENTIVE")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2024,
        branch_id=branch.id, conduction_number="CMTUI-000")
    DocumentTypeService().create(code="MO", name="Maintenance Order",
                                 requires_approval=False, auto_numbering=True)
    from app.modules.document_config.models import DocumentType
    dt = DocumentType.query.filter_by(code="MO").first()
    NumberingSchemeService().create(document_type_id=dt.id, prefix="MO",
                                    include_year=True, digit_count=6,
                                    reset_policy="YEARLY")
    return MaintenanceOrderService().create(
        vehicle_id=vehicle.id, maintenance_type_id=mt.id,
        scheduled_date=date.today(), user=None)


def test_comment_thread_renders_on_detail_page(client, db):
    branch = BranchService().create(code="BR-CMTUI", name="Comment UI Branch")
    vt = VehicleTypeService().create(code="LV-CMTUI", name="Light", category="LIGHT")
    order = _make_maintenance_order(db, branch, vt)
    _login(client, db, codes=["maintenanceorder.view"])

    resp = client.get(f"/transactions/maintenance-orders/{order.id}")
    assert resp.status_code == 200
    assert b"Comment / Attachment" in resp.data
    assert b"Recipient (Optional)" in resp.data
    assert b"Post a new comment" in resp.data
    assert b"Attach File" in resp.data
    assert b"No comments yet" in resp.data


def test_posting_a_comment_shows_up_in_thread(client, db):
    branch = BranchService().create(code="BR-CMTUI2", name="Comment UI Branch 2")
    vt = VehicleTypeService().create(code="LV-CMTUI2", name="Light", category="LIGHT")
    order = _make_maintenance_order(db, branch, vt)
    _login(client, db, codes=["maintenanceorder.view"])

    resp = client.post(f"/comments/maintenance_orders/{order.id}", data={
        "body": "Please prioritize this order.",
        "next": f"/transactions/maintenance-orders/{order.id}",
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert b"Comment posted" in resp.data
    assert b"Please prioritize this order." in resp.data


def test_posting_a_comment_with_recipient(client, db):
    branch = BranchService().create(code="BR-CMTUI3", name="Comment UI Branch 3")
    vt = VehicleTypeService().create(code="LV-CMTUI3", name="Light", category="LIGHT")
    order = _make_maintenance_order(db, branch, vt)
    user = _login(client, db, codes=["maintenanceorder.view"])
    recipient = User(username="comment_recipient_ui", email="comment_recipient_ui@x.com",
                     password_hash=hash_password("pw123456"))
    db.session.add(recipient)
    db.session.commit()

    resp = client.post(f"/comments/maintenance_orders/{order.id}", data={
        "body": "Can you check the parts availability?",
        "recipient_id": str(recipient.id),
        "next": f"/transactions/maintenance-orders/{order.id}",
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert recipient.full_name.encode() in resp.data


def test_empty_comment_is_rejected_with_friendly_message(client, db):
    branch = BranchService().create(code="BR-CMTUI4", name="Comment UI Branch 4")
    vt = VehicleTypeService().create(code="LV-CMTUI4", name="Light", category="LIGHT")
    order = _make_maintenance_order(db, branch, vt)
    _login(client, db, codes=["maintenanceorder.view"])

    resp = client.post(f"/comments/maintenance_orders/{order.id}", data={
        "body": "   ",
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert b"cannot be empty" in resp.data


def test_cannot_comment_on_a_module_without_view_permission(client, db):
    branch = BranchService().create(code="BR-CMTUI5", name="Comment UI Branch 5")
    vt = VehicleTypeService().create(code="LV-CMTUI5", name="Light", category="LIGHT")
    order = _make_maintenance_order(db, branch, vt)
    _login(client, db, codes=[])  # no maintenanceorder.view

    resp = client.post(f"/comments/maintenance_orders/{order.id}", data={
        "body": "Sneaky comment",
    })
    assert resp.status_code == 403
