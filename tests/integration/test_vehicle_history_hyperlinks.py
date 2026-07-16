from datetime import date

import pytest

from app.core.security.password import hash_password
from app.modules.user_management.models import User, Role, Permission
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.reference.service import (
    VehicleTypeService, MaintenanceTypeService)
from app.modules.master_data.org.service import BranchService
from app.modules.transactions.maintenance_order.service import MaintenanceOrderService
from app.modules.document_config.service import (
    DocumentTypeService, NumberingSchemeService)


def _login(client, db, *, codes=()):
    role = Role(name="VehHistoryLinkRole")
    for code in codes:
        m, a = code.split(".")
        p = Permission.query.filter_by(code=code).first()
        if p is None:
            p = Permission(code=code, module=m, action=a)
            db.session.add(p)
        role.permissions.append(p)
    u = User(username="vehhistory_user", email="vehhistory_user@x.com",
             password_hash=hash_password("pw123456"))
    u.roles.append(role)
    db.session.add_all([role, u])
    db.session.commit()
    client.post("/login", data={"username": "vehhistory_user", "password": "pw123456"})
    return u


@pytest.fixture()
def env(db):
    branch = BranchService().create(code="BR-VEHHIST", name="Veh History Branch")
    vt = VehicleTypeService().create(code="LV-VEHHIST", name="Light", category="LIGHT")
    pm_type = MaintenanceTypeService().create(code="VEHHIST-PM", name="PM Test Type",
                                              category="PM")
    cm_type = MaintenanceTypeService().create(code="VEHHIST-CM", name="CM Test Type",
                                              category="CM")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2024,
        branch_id=branch.id, conduction_number="VEHHIST-000")
    DocumentTypeService().create(code="MO", name="Maintenance Order",
                                 requires_approval=False, auto_numbering=True)
    from app.modules.document_config.models import DocumentType
    dt = DocumentType.query.filter_by(code="MO").first()
    NumberingSchemeService().create(document_type_id=dt.id, prefix="MO",
                                    include_year=True, digit_count=6,
                                    reset_policy="YEARLY")
    return branch, vt, vehicle, pm_type, cm_type


def _complete_order(vehicle_id, mtype_id):
    order = MaintenanceOrderService().create(
        vehicle_id=vehicle_id, maintenance_type_id=mtype_id,
        scheduled_date=date.today(), user=None)
    order.status = "COMPLETED"
    order.completed_date = date.today()
    from app.extensions import db
    db.session.commit()
    return order


def test_work_order_number_is_a_hyperlink_when_user_has_permission(client, db, env):
    branch, vt, vehicle, pm_type, cm_type = env
    order = _complete_order(vehicle.id, pm_type.id)
    _login(client, db, codes=["vehicle.view", "maintenanceorder.view"])

    resp = client.get(f"/master/vehicles/{vehicle.id}")
    assert resp.status_code == 200
    expected_link = f'/transactions/maintenance-orders/{order.id}'.encode()
    assert expected_link in resp.data


def test_work_order_number_is_plain_text_without_permission(client, db, env):
    """Permission checking shall apply -- someone without maintenanceorder
    .view sees the document number as plain text, not a clickable link
    into a page they can't actually access."""
    branch, vt, vehicle, pm_type, cm_type = env
    order = _complete_order(vehicle.id, pm_type.id)
    _login(client, db, codes=["vehicle.view"])  # no maintenanceorder.view

    resp = client.get(f"/master/vehicles/{vehicle.id}")
    assert resp.status_code == 200
    link = f'/transactions/maintenance-orders/{order.id}'.encode()
    assert link not in resp.data
    assert order.document_number.encode() in resp.data  # still shown, just not linked


def test_maintenance_history_shows_category_badge(client, db, env):
    """Distinguishes Preventive vs Corrective (and any other configured
    category) at a glance, per the client's PM/CM R&M History request."""
    branch, vt, vehicle, pm_type, cm_type = env
    _complete_order(vehicle.id, pm_type.id)
    _complete_order(vehicle.id, cm_type.id)
    _login(client, db, codes=["vehicle.view", "maintenanceorder.view"])

    resp = client.get(f"/master/vehicles/{vehicle.id}")
    assert resp.status_code == 200
    assert b">PM<" in resp.data
    assert b">CM<" in resp.data
