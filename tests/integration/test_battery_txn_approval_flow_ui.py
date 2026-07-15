from datetime import date

from app.core.security.password import hash_password
from app.modules.user_management.models import User, Role, Permission
from app.modules.master_data.battery.service import BatteryService
from app.modules.master_data.vendor.service import VendorService
from app.modules.approval_config.service import (
    ApprovalPathService, ApprovalMatrixService)
from app.modules.document_config.service import (
    DocumentTypeService, NumberingSchemeService)


def _make_user(db, username, *, codes=()):
    role = Role(name=f"BattApprovalUIRole-{username}")
    for code in codes:
        m, a = code.split(".")
        p = Permission.query.filter_by(code=code).first()
        if p is None:
            p = Permission(code=code, module=m, action=a)
            db.session.add(p)
        role.permissions.append(p)
    u = User(username=username, email=f"{username}@x.com",
             password_hash=hash_password("pw123456"))
    u.roles.append(role)
    db.session.add_all([role, u])
    db.session.commit()
    return u, role


def test_battery_transaction_full_approval_flow_via_http(client, db):
    requester, _ = _make_user(db, "batt_flow_requester",
                              codes=["batterytxn.view", "batterytxn.create",
                                    "batterytxn.update"])
    approver, approver_role = _make_user(db, "batt_flow_approver",
                                         codes=["batterytxn.view"])

    vendor = VendorService().create(code="VEN-BATFLOW", name="Batt Flow Vendor")
    battery = BatteryService().create(serial_number="BATT-FLOW-1",
                                      brand="Motolite", vendor_id=vendor.id)

    dt = DocumentTypeService().create(code="BAT", name="Battery Transaction",
                                      requires_approval=True,
                                      auto_numbering=True)
    NumberingSchemeService().create(document_type_id=dt.id, prefix="BAT",
                                    include_year=True, digit_count=6,
                                    reset_policy="YEARLY")
    path = ApprovalPathService().create(name="Batt Flow Path", levels=[
        {"level_number": 1, "approver_type": "ROLE", "role_id": approver_role.id}])
    ApprovalMatrixService().create(dt.id, path.id, min_amount=None, max_amount=None)

    # Requester creates the transaction — should land as DRAFT
    client.post("/login", data={"username": "batt_flow_requester",
                                "password": "pw123456"})
    client.post("/transactions/battery-transactions/new", data={
        "battery_id": str(battery.id), "vehicle_id": "",
        "action": "MOUNT", "transaction_date": "2026-07-15",
    })
    from app.modules.transactions.battery_txn.models import BatteryTransaction
    txn = BatteryTransaction.query.filter_by(battery_id=battery.id).first()
    assert txn.status == "DRAFT"
    assert BatteryService().get(battery.id).status == "IN_STOCK"  # not yet applied

    # Requester submits it
    resp = client.post(f"/transactions/battery-transactions/{txn.id}/submit",
                       follow_redirects=True)
    assert resp.status_code == 200
    assert b"Battery Transaction submitted" in resp.data

    detail_resp = client.get(f"/transactions/battery-transactions/{txn.id}")
    # The requester is not the eligible approver for this level — they
    # should see the approval line but NOT actionable buttons.
    assert b"Initiator / Reviewer / Approver" in detail_resp.data
    assert b">Approve<" not in detail_resp.data
    assert b">Reject<" not in detail_resp.data
    assert b"Waiting on" in detail_resp.data

    client.get("/logout")

    # Approver sees the buttons and approves — physical effect now applies
    client.post("/login", data={"username": "batt_flow_approver",
                                "password": "pw123456"})
    approver_view = client.get(f"/transactions/battery-transactions/{txn.id}")
    assert b">Approve<" in approver_view.data
    assert b">Reject<" in approver_view.data

    resp = client.post(f"/transactions/battery-transactions/{txn.id}/approve",
                       follow_redirects=True)
    assert resp.status_code == 200
    assert b"Battery Transaction approved" in resp.data

    updated_txn = BatteryTransaction.query.get(txn.id)
    assert updated_txn.status == "COMPLETED"
    assert BatteryService().get(battery.id).status == "MOUNTED"
