from app.core.security.password import hash_password
from app.modules.user_management.models import User, Role, Permission
from app.modules.master_data.reference.service import MaintenanceTypeService
from app.modules.master_data.vehicle_brand.service import (
    VehicleBrandService, VehicleModelService)
from app.modules.maintenance_config.service import PMScheduleService


def _login(client, db, *, codes=()):
    role = Role(name="PMS2UIRole")
    for code in codes:
        m, a = code.split(".")
        p = Permission.query.filter_by(code=code).first()
        if p is None:
            p = Permission(code=code, module=m, action=a)
            db.session.add(p)
        role.permissions.append(p)
    u = User(username="pms2_ui_user", email="pms2_ui_user@x.com",
             password_hash=hash_password("pw123456"))
    u.roles.append(role)
    db.session.add_all([role, u])
    db.session.commit()
    client.post("/login", data={"username": "pms2_ui_user", "password": "pw123456"})
    return u


def test_pms_profiles_link_in_sidebar(client, db):
    _login(client, db, codes=["pmprofile.view"])
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"PMS Profiles" in resp.data
    assert b'href="/admin/pms-profiles"' in resp.data


def test_pms_profile_list_and_detail(client, db):
    _login(client, db, codes=["pmprofile.view"])
    mt = MaintenanceTypeService().create(code="PMS2UI-PREV", name="Preventive",
                                         category="PREVENTIVE")
    toyota = VehicleBrandService().create(name="Toyota PMS2UI")
    hilux = VehicleModelService().create(brand_id=toyota.id, name="Hilux PMS2UI")

    PMScheduleService().create(
        maintenance_type_id=mt.id, trigger_mode="KM", interval_km=5000,
        vehicle_brand_id=toyota.id, vehicle_model_id=hilux.id,
        profile_code="HILUX-DIESEL-PMS2UI", sequence_position=1,
        profile_description="Hilux Diesel PMS")
    PMScheduleService().create(
        maintenance_type_id=mt.id, trigger_mode="KM", interval_km=20000,
        vehicle_brand_id=toyota.id, vehicle_model_id=hilux.id,
        profile_code="HILUX-DIESEL-PMS2UI", sequence_position=2,
        profile_description="Hilux Diesel PMS")

    resp = client.get("/admin/pms-profiles")
    assert resp.status_code == 200
    assert b"HILUX-DIESEL-PMS2UI" in resp.data
    assert b">2<" in resp.data  # package count badge

    detail_resp = client.get("/admin/pms-profiles/HILUX-DIESEL-PMS2UI")
    assert detail_resp.status_code == 200
    assert b"5,000" in detail_resp.data
    assert b"20,000" in detail_resp.data


def test_unknown_profile_code_redirects_with_warning(client, db):
    _login(client, db, codes=["pmprofile.view"])
    resp = client.get("/admin/pms-profiles/DOES-NOT-EXIST", follow_redirects=True)
    assert resp.status_code == 200
    assert b"No PMS Profile found" in resp.data
