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

    # The New Vehicle form itself now starts with an empty dropdown
    # (nothing to filter by until Brand/Model is picked) -- the AJAX
    # endpoint is what actually powers the live-filtered list, which is
    # where the original distinguishability concern applies.
    resp = client.get(
        "/api/search/pm-schedules-for-vehicle-criteria"
        "?brand_name=Toyota PMLabel&model_name=Hilux PMLabel")
    assert resp.status_code == 200
    data = resp.get_json()
    labels = [r["text"] for r in data["results"]]
    assert any("10000" in l or "10,000" in l for l in labels)
    assert any("20000" in l or "20,000" in l for l in labels)
    assert len(set(labels)) == len(labels)  # each option's label is unique
