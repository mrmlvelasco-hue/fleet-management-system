from app.core.security.password import hash_password
from app.modules.user_management.models import User, Role, Permission


def _login(client, db, *, codes=()):
    role = Role(name="EmptyStateRole")
    for code in codes:
        m, a = code.split(".")
        p = Permission(code=code, module=m, action=a)
        db.session.add(p)
        role.permissions.append(p)
    u = User(username="hina", email="hina@x.com",
             password_hash=hash_password("pw123456"))
    u.roles.append(role)
    db.session.add_all([role, u])
    db.session.commit()
    client.post("/login", data={"username": "hina", "password": "pw123456"})
    return u


def test_pmschedule_form_shows_empty_state_when_no_maintenance_types(client, db):
    _login(client, db, codes=["pmschedule.view", "pmschedule.create"])
    resp = client.get("/admin/pm-schedules/new")
    assert resp.status_code == 200
    assert b"No Maintenance Types yet" in resp.data
    assert b"create one first" in resp.data


def test_pmscope_form_shows_empty_state_when_no_maintenance_types(client, db):
    _login(client, db, codes=["pmscopetemplate.view", "pmscopetemplate.create"])
    resp = client.get("/admin/pm-scope-templates/new")
    assert resp.status_code == 200
    assert b"No Maintenance Types yet" in resp.data


def test_pmschedule_form_shows_dropdown_once_maintenance_type_exists(client, db):
    _login(client, db, codes=["pmschedule.view", "pmschedule.create"])
    from app.modules.master_data.reference.service import MaintenanceTypeService
    MaintenanceTypeService().create(code="PMS-EMPTY", name="Test PMS",
                                    category="PREVENTIVE")
    resp = client.get("/admin/pm-schedules/new")
    assert resp.status_code == 200
    assert b"No Maintenance Types yet" not in resp.data
    assert b'name="maintenance_type_id"' in resp.data
