from app.core.security.password import hash_password
from app.modules.user_management.models import User, Role, Permission
from app.modules.master_data.reference.service import MaintenanceTypeService
from app.modules.maintenance_config.service import (
    PMScheduleService, PMScopeTemplateService)


def _login(client, db, *, codes=()):
    role = Role(name="InlineScopeRole")
    for code in codes:
        m, a = code.split(".")
        p = Permission.query.filter_by(code=code).first()
        if p is None:
            p = Permission(code=code, module=m, action=a)
            db.session.add(p)
        role.permissions.append(p)
    u = User(username="inlinescope_user", email="inlinescope_user@x.com",
             password_hash=hash_password("pw123456"))
    u.roles.append(role)
    db.session.add_all([role, u])
    db.session.commit()
    client.post("/login", data={"username": "inlinescope_user", "password": "pw123456"})
    return u


def test_pm_template_detail_shows_scope_activities_inline(client, db):
    """Per user request: viewing a linked Scope Template's activities
    shouldn't require navigating to a separate page -- an inline
    expand/collapse panel should show the checklist directly."""
    _login(client, db, codes=["pmschedule.view"])
    mt = MaintenanceTypeService().create(code="INLINESCOPE-MT", name="Inline Scope Test",
                                         category="PM")
    sched = PMScheduleService().create(
        maintenance_type_id=mt.id, trigger_mode="KM", interval_km=5000)
    PMScopeTemplateService().create(
        maintenance_type_id=mt.id, name="Inline Scope Checklist",
        pm_schedule_id=sched.id,
        items=[{"activity_code": "OIL", "activity_description": "Change Oil",
               "sort_order": 1},
              {"activity_code": "FILTER", "activity_description": "Replace Filter",
               "sort_order": 2}])

    resp = client.get(f"/admin/pm-schedules/{sched.id}")
    assert resp.status_code == 200
    # The activity details are rendered directly on THIS page (not just
    # a link to another page) -- proves the inline expand panel exists.
    assert b"Change Oil" in resp.data
    assert b"Replace Filter" in resp.data
    assert b"data-bs-toggle=\"collapse\"" in resp.data
    assert b"Open full page" in resp.data  # still offered, just secondary
