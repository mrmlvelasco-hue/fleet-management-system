from datetime import date

import pytest

from app.core.security.password import hash_password
from app.modules.user_management.models import User, Role, Permission
from app.modules.approval_config.service import (
    ApprovalPathService, ApprovalMatrixService)
from app.modules.document_config.service import (
    DocumentTypeService, NumberingSchemeService)
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.reference.service import (
    VehicleTypeService, MaintenanceTypeService)
from app.modules.master_data.org.service import BranchService
from app.modules.transactions.maintenance_order.service import MaintenanceOrderService


@pytest.fixture()
def env(db):
    branch = BranchService().create(code="BR-APLINE", name="Approval Line Branch")
    vt = VehicleTypeService().create(code="LV-APLINE", name="Light", category="LIGHT")
    mt = MaintenanceTypeService().create(code="APLINE-5K", name="5K PMS",
                                         category="PREVENTIVE")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2024,
        branch_id=branch.id, conduction_number="APLINE-000")

    role1 = Role(name="Level1 Approver")
    approver1 = User(username="apline_l1", email="apline_l1@x.com",
                     password_hash=hash_password("pw123456"))
    approver1.roles.append(role1)
    outsider_role = Role(name="Outsider Role")
    outsider = User(username="apline_outsider", email="apline_outsider@x.com",
                    password_hash=hash_password("pw123456"))
    outsider.roles.append(outsider_role)
    requester = User(username="apline_requester", email="apline_requester@x.com",
                     password_hash=hash_password("pw123456"))
    db.session.add_all([role1, approver1, outsider_role, outsider, requester])
    db.session.commit()

    for u in (approver1, outsider):
        for code in ["maintenanceorder.view", "maintenanceorder.update"]:
            m, a = code.split(".")
            p = Permission.query.filter_by(code=code).first()
            if p is None:
                p = Permission(code=code, module=m, action=a)
                db.session.add(p)
            u.roles[0].permissions.append(p)
    db.session.commit()

    dt = DocumentTypeService().create(code="MO", name="Maintenance Order",
                                      requires_approval=True, auto_numbering=True)
    NumberingSchemeService().create(document_type_id=dt.id, prefix="MO",
                                    include_year=True, digit_count=6,
                                    reset_policy="YEARLY")
    path = ApprovalPathService().create(name="APLine Path", levels=[
        {"level_number": 1, "approver_type": "ROLE", "role_id": role1.id}])
    ApprovalMatrixService().create(dt.id, path.id, min_amount=None, max_amount=None)

    order = MaintenanceOrderService().create(
        vehicle_id=vehicle.id, maintenance_type_id=mt.id,
        scheduled_date=date.today(), user=requester)
    MaintenanceOrderService().submit(order.id, user=requester)

    return order, approver1, outsider, role1


def test_eligible_approver_sees_action_buttons(client, db, env):
    order, approver1, outsider, role1 = env
    client.post("/login", data={"username": "apline_l1", "password": "pw123456"})
    resp = client.get(f"/transactions/maintenance-orders/{order.id}")
    assert resp.status_code == 200
    assert b">Approve<" in resp.data
    assert b">Reject<" in resp.data
    assert b">Return<" in resp.data


def test_ineligible_user_does_not_see_action_buttons(client, db, env):
    order, approver1, outsider, role1 = env
    client.post("/login", data={"username": "apline_outsider", "password": "pw123456"})
    resp = client.get(f"/transactions/maintenance-orders/{order.id}")
    assert resp.status_code == 200
    assert b">Approve<" not in resp.data
    assert b">Reject<" not in resp.data
    assert b">Return<" not in resp.data


def test_ineligible_user_sees_clear_waiting_message(client, db, env):
    order, approver1, outsider, role1 = env
    client.post("/login", data={"username": "apline_outsider", "password": "pw123456"})
    resp = client.get(f"/transactions/maintenance-orders/{order.id}")
    assert resp.status_code == 200
    assert b"Waiting on" in resp.data
    assert b"Level1 Approver" in resp.data
    assert b"you don" in resp.data  # "you don't hold the approver role..."


def test_approval_line_table_shows_the_level(client, db, env):
    order, approver1, outsider, role1 = env
    client.post("/login", data={"username": "apline_l1", "password": "pw123456"})
    resp = client.get(f"/transactions/maintenance-orders/{order.id}")
    assert resp.status_code == 200
    assert b"Initiator / Reviewer / Approver" in resp.data
    assert b"Level1 Approver" in resp.data
    assert b"Pending with" in resp.data
