from app.core.security.password import hash_password
from app.modules.user_management.models import User, Role, Permission
from app.modules.master_data.reference.service import MaintenanceTypeService
from app.modules.master_data.vehicle_brand.service import (
    VehicleBrandService, VehicleModelService)
from app.modules.maintenance_config.service import PMScheduleService


def _login(client, db, *, codes=()):
    role = Role(name="PMTemplateLabelRole")
    for code in codes:
        m, a = code.split(".")
        p = Permission.query.filter_by(code=code).first()
        if p is None:
            p = Permission(code=code, module=m, action=a)
            db.session.add(p)
        role.permissions.append(p)
    u = User(username="pmlabel_user", email="pmlabel_user@x.com",
             password_hash=hash_password("pw123456"))
    u.roles.append(role)
    db.session.add_all([role, u])
    db.session.commit()
    client.post("/login", data={"username": "pmlabel_user", "password": "pw123456"})
    return u


def test_pm_template_options_are_distinguishable_when_multiple_share_make_model(client, db):
    _login(client, db, codes=["vehicle.view", "vehicle.create"])
    mt = MaintenanceTypeService().create(code="PMLABEL-PREV", name="Preventive Maintenance",
                                         category="PREVENTIVE")
    toyota = VehicleBrandService().create(name="Toyota PMLabel")
    hilux = VehicleModelService().create(brand_id=toyota.id, name="Hilux PMLabel")

    # Two packages, same brand/model/maintenance type — exactly the
    # scenario reported: same-looking entries in the dropdown.
    PMScheduleService().create(
        maintenance_type_id=mt.id, trigger_mode="KM", interval_km=10000,
        vehicle_brand_id=toyota.id, vehicle_model_id=hilux.id,
        profile_code="HILUX-PMLABEL", sequence_position=1)
    PMScheduleService().create(
        maintenance_type_id=mt.id, trigger_mode="KM", interval_km=20000,
        vehicle_brand_id=toyota.id, vehicle_model_id=hilux.id,
        profile_code="HILUX-PMLABEL", sequence_position=2)

    resp = client.get("/master/vehicles/new")
    assert resp.status_code == 200
    assert b"10,000 km" in resp.data
    assert b"20,000 km" in resp.data
    assert b"HILUX-PMLABEL #1" in resp.data
    assert b"HILUX-PMLABEL #2" in resp.data
