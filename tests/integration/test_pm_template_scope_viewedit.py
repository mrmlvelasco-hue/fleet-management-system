from app.core.security.password import hash_password
from app.modules.user_management.models import User, Role, Permission
from app.modules.master_data.reference.service import MaintenanceTypeService
from app.modules.master_data.vehicle_brand.service import (
    VehicleBrandService, VehicleModelService)
from app.modules.maintenance_config.service import (
    PMScheduleService, PMScopeTemplateService)


def _login(client, db, *, codes=()):
    role = Role(name="PMViewEditRole")
    for code in codes:
        m, a = code.split(".")
        p = Permission.query.filter_by(code=code).first()
        if p is None:
            p = Permission(code=code, module=m, action=a)
            db.session.add(p)
        role.permissions.append(p)
    u = User(username="pm_viewedit_user", email="pm_viewedit_user@x.com",
             password_hash=hash_password("pw123456"))
    u.roles.append(role)
    db.session.add_all([role, u])
    db.session.commit()
    client.post("/login", data={"username": "pm_viewedit_user", "password": "pw123456"})
    return u


# ── PM Template (PMSchedule) ────────────────────────────────────────────

def test_schedule_list_has_view_and_edit_icons(client, db):
    _login(client, db, codes=["pmschedule.view", "pmschedule.update"])
    mt = MaintenanceTypeService().create(code="PMVE-5K", name="5K PMS",
                                         category="PREVENTIVE")
    sched = PMScheduleService().create(
        maintenance_type_id=mt.id, trigger_mode="KM", interval_km=5000)

    resp = client.get("/admin/pm-schedules")
    assert resp.status_code == 200
    assert f'/admin/pm-schedules/{sched.id}'.encode() in resp.data
    assert b"bi-eye" in resp.data
    assert b"bi-pencil" in resp.data


def test_schedule_detail_shows_all_fields(client, db):
    _login(client, db, codes=["pmschedule.view"])
    mt = MaintenanceTypeService().create(code="PMVE-5K2", name="5K PMS 2",
                                         category="PREVENTIVE")
    toyota = VehicleBrandService().create(name="Toyota PMVE")
    hilux = VehicleModelService().create(brand_id=toyota.id, name="Hilux PMVE")
    sched = PMScheduleService().create(
        maintenance_type_id=mt.id, trigger_mode="KM", interval_km=5000,
        vehicle_brand_id=toyota.id, vehicle_model_id=hilux.id,
        profile_code="HILUX-VIEWEDIT")

    resp = client.get(f"/admin/pm-schedules/{sched.id}")
    assert resp.status_code == 200
    assert b"HILUX-VIEWEDIT" in resp.data
    assert b"5,000" in resp.data or b"5000" in resp.data


def test_schedule_edit_get_prepopulates_form(client, db):
    _login(client, db, codes=["pmschedule.view", "pmschedule.update"])
    mt = MaintenanceTypeService().create(code="PMVE-5K3", name="5K PMS 3",
                                         category="PREVENTIVE")
    sched = PMScheduleService().create(
        maintenance_type_id=mt.id, trigger_mode="KM", interval_km=5000,
        profile_code="EDIT-PREFILL-TEST")

    resp = client.get(f"/admin/pm-schedules/{sched.id}/edit")
    assert resp.status_code == 200
    assert b'value="EDIT-PREFILL-TEST"' in resp.data
    assert b'value="5000"' in resp.data


def test_schedule_edit_post_updates_record(client, db):
    _login(client, db, codes=["pmschedule.view", "pmschedule.update"])
    mt = MaintenanceTypeService().create(code="PMVE-5K4", name="5K PMS 4",
                                         category="PREVENTIVE")
    sched = PMScheduleService().create(
        maintenance_type_id=mt.id, trigger_mode="KM", interval_km=5000)

    resp = client.post(f"/admin/pm-schedules/{sched.id}/edit", data={
        "maintenance_type_id": str(mt.id), "trigger_mode": "KM",
        "interval_km": "7500", "priority": "HIGH",
    }, follow_redirects=True)
    assert resp.status_code == 200

    from app.modules.maintenance_config.models import PMSchedule
    updated = PMSchedule.query.get(sched.id)
    assert updated.interval_km == 7500
    assert updated.priority == "HIGH"


# ── PM Scope Template ────────────────────────────────────────────────────

def test_scope_list_has_view_and_edit_icons(client, db):
    _login(client, db, codes=["pmscopetemplate.view", "pmscopetemplate.update"])
    mt = MaintenanceTypeService().create(code="PMVE-SCOPE1", name="Scope 1",
                                         category="PREVENTIVE")
    tmpl = PMScopeTemplateService().create(
        maintenance_type_id=mt.id, name="Test Scope",
        items=[{"activity_code": "OIL", "activity_description": "Change Oil",
               "sort_order": 1}])

    resp = client.get("/admin/pm-scope-templates")
    assert resp.status_code == 200
    assert f'/admin/pm-scope-templates/{tmpl.id}'.encode() in resp.data
    assert b"bi-eye" in resp.data
    assert b"bi-pencil" in resp.data


def test_scope_detail_shows_all_activity_items(client, db):
    _login(client, db, codes=["pmscopetemplate.view"])
    mt = MaintenanceTypeService().create(code="PMVE-SCOPE2", name="Scope 2",
                                         category="PREVENTIVE")
    tmpl = PMScopeTemplateService().create(
        maintenance_type_id=mt.id, name="Detail Scope Test",
        items=[
            {"activity_code": "OIL", "activity_description": "Change Oil",
            "sort_order": 1},
            {"activity_code": "BRAKE", "activity_description": "Check Brakes",
            "sort_order": 2},
        ])

    resp = client.get(f"/admin/pm-scope-templates/{tmpl.id}")
    assert resp.status_code == 200
    assert b"Change Oil" in resp.data
    assert b"Check Brakes" in resp.data


def test_scope_edit_get_prepopulates_form(client, db):
    _login(client, db, codes=["pmscopetemplate.view", "pmscopetemplate.update"])
    mt = MaintenanceTypeService().create(code="PMVE-SCOPE3", name="Scope 3",
                                         category="PREVENTIVE")
    tmpl = PMScopeTemplateService().create(
        maintenance_type_id=mt.id, name="Edit Prefill Scope",
        items=[{"activity_code": "OIL", "activity_description": "Change Oil",
               "sort_order": 1}])

    resp = client.get(f"/admin/pm-scope-templates/{tmpl.id}/edit")
    assert resp.status_code == 200
    assert b"Edit Prefill Scope" in resp.data
    assert b"Change Oil" in resp.data


def test_scope_edit_post_updates_record(client, db):
    _login(client, db, codes=["pmscopetemplate.view", "pmscopetemplate.update"])
    mt = MaintenanceTypeService().create(code="PMVE-SCOPE4", name="Scope 4",
                                         category="PREVENTIVE")
    tmpl = PMScopeTemplateService().create(
        maintenance_type_id=mt.id, name="Original Name",
        items=[{"activity_code": "OIL", "activity_description": "Change Oil",
               "sort_order": 1}])

    resp = client.post(f"/admin/pm-scope-templates/{tmpl.id}/edit", data={
        "name": "Renamed Scope", "maintenance_type_id": str(mt.id),
        "activity_code": ["OIL", "FILTER"],
        "activity_description": ["Change Oil Updated", "Replace Filter"],
    }, follow_redirects=True)
    assert resp.status_code == 200

    from app.modules.maintenance_config.models import PMScopeTemplate
    updated = PMScopeTemplate.query.get(tmpl.id)
    assert updated.name == "Renamed Scope"
    assert len(updated.items) == 2
