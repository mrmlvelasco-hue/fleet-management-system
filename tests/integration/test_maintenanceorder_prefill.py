from datetime import date

import pytest

from app.core.security.password import hash_password
from app.modules.user_management.models import User, Role, Permission
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.reference.service import (
    VehicleTypeService, MaintenanceTypeService)
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.maintenance_config.service import PMScheduleService
from app.modules.document_config.service import (
    DocumentTypeService, NumberingSchemeService)


def _login(client, db, *, codes=()):
    role = Role(name="MOPrefillRole")
    for code in codes:
        m, a = code.split(".")
        p = Permission.query.filter_by(code=code).first()
        if p is None:
            p = Permission(code=code, module=m, action=a)
            db.session.add(p)
        role.permissions.append(p)
    u = User(username="mo_prefill_user", email="mo_prefill_user@x.com",
             password_hash=hash_password("pw123456"))
    u.roles.append(role)
    db.session.add_all([role, u])
    db.session.commit()
    client.post("/login", data={"username": "mo_prefill_user", "password": "pw123456"})
    return u


@pytest.fixture()
def env(db):
    branch = BranchService().create(code="BR-MOPREFILL", name="MO Prefill Branch")
    vt = VehicleTypeService().create(code="LV-MOPREFILL", name="Light", category="LIGHT")
    mt = MaintenanceTypeService().create(code="MOPREFILL-5K", name="5K PMS",
                                         category="PREVENTIVE")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Honda", model="City", year=2024,
        branch_id=branch.id, conduction_number="MOPREFILL-000",
        current_odometer=900)
    DocumentTypeService().create(code="MO", name="Maintenance Order",
                                 requires_approval=False, auto_numbering=True)
    from app.modules.document_config.models import DocumentType
    dt = DocumentType.query.filter_by(code="MO").first()
    NumberingSchemeService().create(document_type_id=dt.id, prefix="MO",
                                    include_year=True, digit_count=6,
                                    reset_policy="YEARLY")
    sched = PMScheduleService().create(
        vehicle_type_id=vt.id, maintenance_type_id=mt.id, trigger_mode="KM",
        interval_km=1000)
    return branch, vt, mt, vehicle, sched


def test_new_form_prefills_vehicle_and_type_from_query_params(client, db, env):
    branch, vt, mt, vehicle, sched = env
    _login(client, db, codes=["maintenanceorder.view", "maintenanceorder.create"])

    resp = client.get(
        f"/transactions/maintenance-orders/new"
        f"?vehicle_id={vehicle.id}&maintenance_type_id={mt.id}"
        f"&odometer_at_service=900")
    assert resp.status_code == 200
    assert f'value="{vehicle.id}" selected'.encode() in resp.data
    assert f'value="{mt.id}" selected'.encode() in resp.data
    assert b'value="900"' in resp.data


def test_prefilled_form_still_submits_correctly(client, db, env):
    branch, vt, mt, vehicle, sched = env
    _login(client, db, codes=["maintenanceorder.view", "maintenanceorder.create"])

    resp = client.post(
        f"/transactions/maintenance-orders/new"
        f"?vehicle_id={vehicle.id}&maintenance_type_id={mt.id}",
        data={
            "vehicle_id": str(vehicle.id), "maintenance_type_id": str(mt.id),
            "scheduled_date": date.today().isoformat(),
            "odometer_at_service": "900",
        }, follow_redirects=True)
    assert resp.status_code == 200

    from app.modules.transactions.maintenance_order.models import MaintenanceOrder
    order = MaintenanceOrder.query.filter_by(vehicle_id=vehicle.id).first()
    assert order is not None


def test_dashboard_due_vehicle_link_points_to_prefilled_mo_form(client, db, env):
    branch, vt, mt, vehicle, sched = env
    vehicle.current_odometer = 950
    db.session.commit()
    _login(client, db, codes=["maintenanceorder.view", "maintenanceorder.create",
                              "vehicle.view"])

    resp = client.get("/")
    assert resp.status_code == 200
    assert f"/transactions/maintenance-orders/new?vehicle_id={vehicle.id}".encode() in resp.data


def test_dashboard_due_vehicle_link_falls_back_without_mo_create_permission(client, db, env):
    """A user who can't create Maintenance Orders shouldn't be linked to
    a form they'll just get a 403 on — fall back to the vehicle detail
    page instead."""
    branch, vt, mt, vehicle, sched = env
    vehicle.current_odometer = 950
    db.session.commit()
    _login(client, db, codes=["vehicle.view"])  # no maintenanceorder.create

    resp = client.get("/")
    assert resp.status_code == 200
    assert f"/master/vehicles/{vehicle.id}".encode() in resp.data
    assert b"/transactions/maintenance-orders/new" not in resp.data
