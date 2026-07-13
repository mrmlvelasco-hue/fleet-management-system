from app.core.security.password import hash_password
from app.modules.user_management.models import User, Role, Permission
from app.modules.master_data.reference.service import MaintenanceTypeService


def _login(client, db, *, codes=()):
    role = Role(name="PMS3UIRole")
    for code in codes:
        m, a = code.split(".")
        p = Permission.query.filter_by(code=code).first()
        if p is None:
            p = Permission(code=code, module=m, action=a)
            db.session.add(p)
        role.permissions.append(p)
    u = User(username="pms3_ui_user", email="pms3_ui_user@x.com",
             password_hash=hash_password("pw123456"))
    u.roles.append(role)
    db.session.add_all([role, u])
    db.session.commit()
    client.post("/login", data={"username": "pms3_ui_user", "password": "pw123456"})
    return u


def test_pm_template_form_has_scheduling_policy_fields(client, db):
    _login(client, db, codes=["pmschedule.view", "pmschedule.create"])
    resp = client.get("/admin/pm-schedules/new")
    assert resp.status_code == 200
    assert b'name="next_pms_generation"' in resp.data
    assert b'name="next_due_calculation_method"' in resp.data
    assert b"Auto Generate Schedule (Recommended)" in resp.data


def test_create_pm_template_with_manual_generation_policy(client, db):
    _login(client, db, codes=["pmschedule.view", "pmschedule.create"])
    mt = MaintenanceTypeService().create(code="PMS3UI-5K", name="5K PMS",
                                         category="PREVENTIVE")
    resp = client.post("/admin/pm-schedules/new", data={
        "maintenance_type_id": str(mt.id), "trigger_mode": "KM",
        "interval_km": "5000", "priority": "MEDIUM",
        "next_pms_generation": "MANUAL",
        "next_due_calculation_method": "ORIGINAL_SCHEDULE",
    }, follow_redirects=True)
    assert resp.status_code == 200

    from app.modules.maintenance_config.models import PMSchedule
    sched = PMSchedule.query.filter_by(maintenance_type_id=mt.id).first()
    assert sched is not None
    assert sched.next_pms_generation == "MANUAL"
    assert sched.next_due_calculation_method == "ORIGINAL_SCHEDULE"
