from datetime import date

from app.core.security.password import hash_password
from app.modules.user_management.models import User, Role, Permission
from app.modules.master_data.reference.service import (
    VehicleTypeService, MaintenanceTypeService)
from app.modules.master_data.org.service import BranchService
from app.modules.maintenance_config.service import PMScheduleService


def _login(client, db, *, codes=()):
    role = Role(name="LegacyVehicleUIRole")
    for code in codes:
        m, a = code.split(".")
        p = Permission.query.filter_by(code=code).first()
        if p is None:
            p = Permission(code=code, module=m, action=a)
            db.session.add(p)
        role.permissions.append(p)
    u = User(username="legacy_vehicle_ui_user", email="legacy_vehicle_ui_user@x.com",
             password_hash=hash_password("pw123456"))
    u.roles.append(role)
    db.session.add_all([role, u])
    db.session.commit()
    client.post("/login", data={"username": "legacy_vehicle_ui_user", "password": "pw123456"})
    return u


def test_registering_a_legacy_vehicle_through_the_form_avoids_false_due_flag(client, db):
    _login(client, db, codes=["vehicle.view", "vehicle.create"])
    branch = BranchService().create(code="BR-LEGACYUI", name="Legacy UI Branch")
    vt = VehicleTypeService().create(code="LV-LEGACYUI", name="Light", category="LIGHT")
    mt = MaintenanceTypeService().create(code="LEGACYUI-MT", name="Legacy UI Test",
                                         category="PM")
    PMScheduleService().create(vehicle_type_id=vt.id, maintenance_type_id=mt.id,
                               trigger_mode="KM", interval_km=5000)

    from app.modules.master_data.vehicle_brand.service import (
        VehicleBrandService, VehicleModelService)
    toyota = VehicleBrandService().create(name="Toyota LegacyUI")
    hilux = VehicleModelService().create(brand_id=toyota.id, name="Hilux LegacyUI")

    resp = client.post("/master/vehicles/new", data={
        "vehicle_type_id": str(vt.id), "brand": "Toyota LegacyUI", "model": "Hilux LegacyUI",
        "year": "2020", "branch_id": str(branch.id),
        "conduction_number": "LEGACYUI-000", "current_odometer": "48000",
        "last_pm_odometer": "47000",
        "last_pm_date": date(2026, 7, 1).isoformat(),
    }, follow_redirects=True)
    assert resp.status_code == 200

    from app.modules.master_data.vehicle.models import Vehicle
    vehicle = Vehicle.query.filter_by(conduction_number="LEGACYUI-000").first()
    assert vehicle is not None
    assert vehicle.last_pm_odometer == 47000
    assert vehicle.last_pm_date == date(2026, 7, 1)

    from app.core.maintenance.due_calculation_service import PMDueCalculationService
    status = PMDueCalculationService().get_due_status(vehicle, as_of_date=date(2026, 7, 18))
    assert status["status"] == "GOOD"


def test_legacy_baseline_fields_appear_on_vehicle_form(client, db):
    _login(client, db, codes=["vehicle.view", "vehicle.create"])
    resp = client.get("/master/vehicles/new")
    assert resp.status_code == 200
    assert b"Legacy Vehicle Baseline" in resp.data
    assert b'name="last_pm_odometer"' in resp.data
    assert b'name="last_pm_date"' in resp.data
