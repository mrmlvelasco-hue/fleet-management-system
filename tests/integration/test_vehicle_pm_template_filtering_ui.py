from app.core.security.password import hash_password
from app.modules.user_management.models import User, Role, Permission
from app.modules.maintenance_config.service import PMScheduleService
from app.modules.master_data.reference.service import (
    VehicleTypeService, MaintenanceTypeService)
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.org.service import BranchService


def _login(client, db, *, codes=()):
    role = Role(name="VehPmFilterUIRole")
    for code in codes:
        m, a = code.split(".")
        p = Permission.query.filter_by(code=code).first()
        if p is None:
            p = Permission(code=code, module=m, action=a)
            db.session.add(p)
        role.permissions.append(p)
    u = User(username="veh_pm_filter_user", email="veh_pm_filter_user@x.com",
             password_hash=hash_password("pw123456"))
    u.roles.append(role)
    db.session.add_all([role, u])
    db.session.commit()
    client.post("/login", data={"username": "veh_pm_filter_user", "password": "pw123456"})
    return u


def test_edit_form_only_shows_matching_pm_templates(client, db):
    """Reproduces the reported bug: the Assigned PM Template dropdown
    showed every active PM Template regardless of the vehicle's own
    Brand/Model."""
    _login(client, db, codes=["vehicle.view", "vehicle.update"])
    branch = BranchService().create(code="BR-VEHPMFILTER", name="Veh PM Filter Branch")
    vt = VehicleTypeService().create(code="LV-VEHPMFILTER", name="Light", category="LIGHT")
    mt = MaintenanceTypeService().create(code="VEHPMFILTER-MT", name="Veh PM Filter Test",
                                         category="PM")
    PMScheduleService().create(maintenance_type_id=mt.id, trigger_mode="KM",
                               interval_km=1000, vehicle_make="Ford", vehicle_model="Escape")
    PMScheduleService().create(maintenance_type_id=mt.id, trigger_mode="KM",
                               interval_km=5000, vehicle_make="Honda", vehicle_model="City")

    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Ford", model="Escape", year=2024,
        branch_id=branch.id, conduction_number="VEHPMFILTER-000")

    resp = client.get(f"/master/vehicles/{vehicle.id}/edit")
    assert resp.status_code == 200
    assert b"Ford Escape" in resp.data
    assert b"Honda City" not in resp.data


def test_ajax_endpoint_filters_by_criteria(client, db):
    _login(client, db, codes=["vehicle.view"])
    mt = MaintenanceTypeService().create(code="VEHPMFILTER-MT2", name="Veh PM Filter Test 2",
                                         category="PM")
    PMScheduleService().create(maintenance_type_id=mt.id, trigger_mode="KM",
                               interval_km=1000, vehicle_make="Ford", vehicle_model="Escape")
    resp = client.get(
        "/api/search/pm-schedules-for-vehicle-criteria"
        "?brand_name=Ford&model_name=Escape")
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data["results"]) == 1


def test_existing_manual_override_never_disappears_from_dropdown(client, db):
    """If a vehicle's PM Template was explicitly set to something
    outside the normal Brand/Model match criteria (a deliberate manual
    override), editing the vehicle must not silently drop it from the
    dropdown."""
    _login(client, db, codes=["vehicle.view", "vehicle.update"])
    branch = BranchService().create(code="BR-VEHPMOVERRIDE", name="Veh PM Override Branch")
    vt = VehicleTypeService().create(code="LV-VEHPMOVERRIDE", name="Light", category="LIGHT")
    mt = MaintenanceTypeService().create(code="VEHPMOVERRIDE-MT", name="Override Test",
                                         category="PM")
    unrelated_sched = PMScheduleService().create(
        maintenance_type_id=mt.id, trigger_mode="KM", interval_km=9999,
        vehicle_make="SomeOtherBrand", vehicle_model="SomeOtherModel")

    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Ford", model="Escape", year=2024,
        branch_id=branch.id, conduction_number="VEHPMOVERRIDE-000",
        pm_schedule_id=unrelated_sched.id)

    resp = client.get(f"/master/vehicles/{vehicle.id}/edit")
    assert resp.status_code == 200
    assert f'value="{unrelated_sched.id}" selected'.encode() in resp.data
