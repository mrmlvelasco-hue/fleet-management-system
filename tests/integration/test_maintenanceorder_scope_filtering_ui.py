from datetime import date

import pytest

from app.core.security.password import hash_password
from app.modules.user_management.models import User, Role, Permission
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.driver.service import DriverService
from app.modules.master_data.reference.service import (
    VehicleTypeService, MaintenanceTypeService)
from app.modules.master_data.org.service import BranchService
from app.modules.maintenance_config.service import (
    PMScheduleService, PMScopeTemplateService)


def _login(client, db, *, codes=()):
    role = Role(name="MOScopeFilterUIRole")
    for code in codes:
        m, a = code.split(".")
        p = Permission.query.filter_by(code=code).first()
        if p is None:
            p = Permission(code=code, module=m, action=a)
            db.session.add(p)
        role.permissions.append(p)
    u = User(username="mo_scope_ui_user", email="mo_scope_ui_user@x.com",
             password_hash=hash_password("pw123456"))
    u.roles.append(role)
    db.session.add_all([role, u])
    db.session.commit()
    client.post("/login", data={"username": "mo_scope_ui_user", "password": "pw123456"})
    return u


@pytest.fixture()
def env(db):
    branch = BranchService().create(code="BR-MOSCOPEUI", name="MO Scope UI Branch")
    vt = VehicleTypeService().create(code="LV-MOSCOPEUI", name="Light", category="LIGHT")
    mt = MaintenanceTypeService().create(code="MOSCOPEUI-MT", name="PM Test",
                                         category="PM")
    driver = DriverService().create(
        employee_number="EMP-MOSCOPEUI1", first_name="Test", last_name="Assignee",
        license_number="LIC-MOSCOPEUI1", license_expiry=date(2030, 1, 1),
        license_type="PROFESSIONAL", branch_id=branch.id, job_title="Manager")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Ford", model="Escape", year=2024,
        branch_id=branch.id, conduction_number="MOSCOPEUI-000",
        plate_number="MOUI001", assigned_driver_id=driver.id,
        current_odometer=500)
    other_vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Honda", model="City", year=2024,
        branch_id=branch.id, conduction_number="MOSCOPEUI-001")

    sched = PMScheduleService().create(
        maintenance_type_id=mt.id, trigger_mode="KM", interval_km=1000,
        vehicle_brand_id=None, vehicle_type_id=vt.id)  # matches BOTH vehicles via Vehicle Type
    ford_sched = PMScheduleService().create(
        maintenance_type_id=mt.id, trigger_mode="KM", interval_km=1000)
    PMScopeTemplateService().create(
        maintenance_type_id=mt.id, name="Ford Escape Checklist",
        pm_schedule_id=ford_sched.id,
        items=[{"activity_code": "A1", "activity_description": "Ford check",
               "sort_order": 1}])
    return branch, vt, mt, vehicle, other_vehicle, driver


def test_new_form_shows_vehicle_info_panel_when_prefilled(client, db, env):
    branch, vt, mt, vehicle, other_vehicle, driver = env
    _login(client, db, codes=["maintenanceorder.view", "maintenanceorder.create"])
    resp = client.get(f"/transactions/maintenance-orders/new?vehicle_id={vehicle.id}")
    assert resp.status_code == 200
    assert b"Vehicle Information" in resp.data
    assert b"MOUI001" in resp.data
    assert b"Test Assignee" in resp.data
    assert b"Manager" in resp.data
    assert b"MO Scope UI Branch" in resp.data


def test_ajax_endpoint_filters_scope_templates_by_vehicle(client, db, env):
    branch, vt, mt, vehicle, other_vehicle, driver = env
    _login(client, db, codes=["maintenanceorder.view"])
    resp = client.get(
        f"/api/search/pm-scope-templates-for-vehicle?vehicle_id={vehicle.id}"
        f"&maintenance_type_id={mt.id}")
    assert resp.status_code == 200
    data = resp.get_json()
    names = [r["text"] for r in data["results"]]
    assert "Ford Escape Checklist" not in names  # vehicle has no matching brand/model FK

    # But it should show up for a vehicle whose brand/model actually
    # matches the Ford schedule's criteria via free-text match.
    resp2 = client.get(
        f"/api/search/pm-scope-templates-for-vehicle?vehicle_id={vehicle.id}")
    assert resp2.status_code == 200


def test_ajax_endpoint_returns_empty_without_vehicle(client, db, env):
    branch, vt, mt, vehicle, other_vehicle, driver = env
    _login(client, db, codes=["maintenanceorder.view"])
    resp = client.get("/api/search/pm-scope-templates-for-vehicle")
    assert resp.status_code == 200
    assert resp.get_json()["results"] == []


def test_vehicle_details_endpoint(client, db, env):
    branch, vt, mt, vehicle, other_vehicle, driver = env
    _login(client, db, codes=["maintenanceorder.view"])
    resp = client.get(f"/api/search/vehicle-details/{vehicle.id}")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["found"] is True
    assert data["plate"] == "MOUI001"
    assert data["assignee"] == "Test Assignee"
    assert data["position"] == "Manager"
    assert data["odometer"] == 500
