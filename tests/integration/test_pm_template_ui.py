from app.core.security.password import hash_password
from app.modules.user_management.models import User, Role, Permission
from app.modules.master_data.reference.service import (
    VehicleTypeService, MaintenanceTypeService)
from app.modules.master_data.org.service import BranchService


def _login(client, db, *, codes=()):
    role = Role(name="PmTemplateRole")
    for code in codes:
        m, a = code.split(".")
        p = Permission(code=code, module=m, action=a)
        db.session.add(p)
        role.permissions.append(p)
    u = User(username="tariq", email="tariq@x.com",
             password_hash=hash_password("pw123456"))
    u.roles.append(role)
    db.session.add_all([role, u])
    db.session.commit()
    client.post("/login", data={"username": "tariq", "password": "pw123456"})
    return u


def test_pmschedule_form_has_make_model_fields(client, db):
    _login(client, db, codes=["pmschedule.view", "pmschedule.create"])
    resp = client.get("/admin/pm-schedules/new")
    assert resp.status_code == 200
    assert b'name="vehicle_make"' in resp.data
    assert b'name="vehicle_model"' in resp.data
    assert b'name="notify_before_km"' in resp.data
    assert b'name="escalate_if_overdue"' in resp.data


def test_pmschedule_create_with_make_model_via_post(client, db):
    _login(client, db, codes=["pmschedule.view", "pmschedule.create"])
    mt = MaintenanceTypeService().create(code="PMS-UI", name="PMS UI Test",
                                         category="PREVENTIVE")
    resp = client.post("/admin/pm-schedules/new", data={
        "vehicle_make": "Honda", "vehicle_model": "City",
        "maintenance_type_id": str(mt.id), "trigger_mode": "KM",
        "interval_km": "10000", "priority": "MEDIUM",
        "notify_before_km": "1000",
    }, follow_redirects=True)
    assert resp.status_code == 200
    from app.modules.maintenance_config.models import PMSchedule
    sched = PMSchedule.query.filter_by(vehicle_make="Honda").first()
    assert sched is not None
    assert sched.notify_before_km == 1000


def test_vehicle_form_has_pm_template_field(client, db):
    _login(client, db, codes=["vehicle.view", "vehicle.create"])
    resp = client.get("/master/vehicles/new")
    assert resp.status_code == 200
    assert b'name="pm_schedule_id"' in resp.data


def test_scope_form_has_pm_template_link_field(client, db):
    _login(client, db, codes=["pmscopetemplate.view", "pmscopetemplate.create"])
    resp = client.get("/admin/pm-scope-templates/new")
    assert resp.status_code == 200
    assert b'name="pm_schedule_id"' in resp.data
