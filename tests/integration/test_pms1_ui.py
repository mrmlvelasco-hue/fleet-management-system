from app.core.security.password import hash_password
from app.modules.user_management.models import User, Role, Permission
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.reference.service import (
    VehicleTypeService, MaintenanceTypeService)
from app.modules.master_data.vehicle_brand.service import (
    VehicleBrandService, VehicleModelService)


def _login(client, db, *, codes=()):
    role = Role(name="PMS1UIRole")
    for code in codes:
        m, a = code.split(".")
        p = Permission.query.filter_by(code=code).first()
        if p is None:
            p = Permission(code=code, module=m, action=a)
            db.session.add(p)
        role.permissions.append(p)
    u = User(username="pms1_ui_user", email="pms1_ui_user@x.com",
             password_hash=hash_password("pw123456"))
    u.roles.append(role)
    db.session.add_all([role, u])
    db.session.commit()
    client.post("/login", data={"username": "pms1_ui_user", "password": "pw123456"})
    return u


def test_vehicle_form_has_pms1_fields(client, db):
    _login(client, db, codes=["vehicle.view", "vehicle.create"])
    resp = client.get("/master/vehicles/new")
    assert resp.status_code == 200
    assert b'name="variant"' in resp.data
    assert b'name="engine_type"' in resp.data
    assert b'name="transmission"' in resp.data
    assert b'name="current_engine_hours"' in resp.data


def test_create_vehicle_with_pms1_fields(client, db):
    _login(client, db, codes=["vehicle.view", "vehicle.create"])
    branch = BranchService().create(code="BR-PMS1UI", name="PMS1 UI Branch")
    vt = VehicleTypeService().create(code="LV-PMS1UI", name="Light", category="LIGHT")
    toyota = VehicleBrandService().create(name="Toyota-PMS1UI")
    VehicleModelService().create(brand_id=toyota.id, name="Hilux-PMS1UI")

    resp = client.post("/master/vehicles/new", data={
        "vehicle_type_id": str(vt.id), "brand": "Toyota-PMS1UI",
        "model": "Hilux-PMS1UI",
        "year": "2024", "branch_id": str(branch.id),
        "conduction_number": "PMS1UI-000",
        "variant": "2.8 D-4D", "engine_type": "1GD-FTV",
        "transmission": "AUTOMATIC", "current_engine_hours": "1500",
    }, follow_redirects=True)
    assert resp.status_code == 200

    from app.modules.master_data.vehicle.models import Vehicle
    vehicle = Vehicle.query.filter_by(conduction_number="PMS1UI-000").first()
    assert vehicle is not None
    assert vehicle.variant == "2.8 D-4D"
    assert vehicle.engine_type == "1GD-FTV"
    assert vehicle.transmission == "AUTOMATIC"
    assert vehicle.current_engine_hours == 1500


def test_vehicle_detail_shows_pms1_fields(client, db):
    _login(client, db, codes=["vehicle.view", "vehicle.create"])
    branch = BranchService().create(code="BR-PMS1UI2", name="PMS1 UI Branch 2")
    vt = VehicleTypeService().create(code="LV-PMS1UI2", name="Light", category="LIGHT")
    from app.modules.master_data.vehicle.service import VehicleService
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Honda", model="City", year=2024,
        branch_id=branch.id, conduction_number="PMS1UI-001",
        variant="1.5 VTEC", engine_type="L15B", transmission="CVT",
        current_engine_hours=800)

    resp = client.get(f"/master/vehicles/{vehicle.id}")
    assert resp.status_code == 200
    assert b"1.5 VTEC" in resp.data
    assert b"L15B" in resp.data
    assert b"CVT" in resp.data


def test_pm_template_form_has_fk_brand_model_and_profile_fields(client, db):
    _login(client, db, codes=["pmschedule.view", "pmschedule.create"])
    resp = client.get("/admin/pm-schedules/new")
    assert resp.status_code == 200
    assert b'id="pmBrandSelect"' in resp.data
    assert b'id="pmModelSelect"' in resp.data
    assert b'name="profile_code"' in resp.data
    assert b'name="effective_date"' in resp.data


def test_create_pm_template_with_fk_brand_model_and_profile(client, db):
    _login(client, db, codes=["pmschedule.view", "pmschedule.create"])
    mt = MaintenanceTypeService().create(code="PMS1UI-5K", name="5K PMS",
                                         category="PREVENTIVE")
    toyota = VehicleBrandService().create(name="Toyota PMS1UI")
    hilux = VehicleModelService().create(brand_id=toyota.id, name="Hilux PMS1UI")

    resp = client.post("/admin/pm-schedules/new", data={
        "maintenance_type_id": str(mt.id), "trigger_mode": "KM",
        "interval_km": "5000", "priority": "MEDIUM",
        "vehicle_brand_id": str(toyota.id), "vehicle_model_id": str(hilux.id),
        "variant": "2.8 D-4D", "profile_code": "HILUX-DIESEL-UI",
        "profile_description": "Hilux Diesel PMS", "effective_date": "2026-01-01",
    }, follow_redirects=True)
    assert resp.status_code == 200

    from app.modules.maintenance_config.models import PMSchedule
    sched = PMSchedule.query.filter_by(profile_code="HILUX-DIESEL-UI").first()
    assert sched is not None
    assert sched.vehicle_brand_id == toyota.id
    assert sched.vehicle_model_id == hilux.id
    assert sched.variant == "2.8 D-4D"
