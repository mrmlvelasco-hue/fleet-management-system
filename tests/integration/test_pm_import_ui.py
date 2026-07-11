import io

from app.core.security.password import hash_password
from app.modules.user_management.models import User, Role, Permission
from app.modules.master_data.reference.service import (
    VehicleTypeService, MaintenanceTypeService)


def _login(client, db, *, codes=()):
    role = Role(name="ImportRole")
    for code in codes:
        m, a = code.split(".")
        p = Permission(code=code, module=m, action=a)
        db.session.add(p)
        role.permissions.append(p)
    u = User(username="qasim", email="qasim@x.com",
             password_hash=hash_password("pw123456"))
    u.roles.append(role)
    db.session.add_all([role, u])
    db.session.commit()
    client.post("/login", data={"username": "qasim", "password": "pw123456"})
    return u


def test_schedule_import_page_renders(client, db):
    _login(client, db, codes=["pmschedule.view", "pmschedule.create"])
    assert client.get("/admin/pm-schedules/import").status_code == 200


def test_schedule_import_uploads_csv(client, db):
    _login(client, db, codes=["pmschedule.view", "pmschedule.create"])
    MaintenanceTypeService().create(code="PMS-IMP", name="PMS Import Test",
                                    category="PREVENTIVE")
    csv_content = ("vehicle_type_code,maintenance_type_code,trigger_mode,"
                  "interval_km,interval_days,priority\n"
                  ",PMS-IMP,KM,5000,,MEDIUM\n")
    resp = client.post("/admin/pm-schedules/import", data={
        "csv_file": (io.BytesIO(csv_content.encode()), "schedules.csv"),
    }, content_type="multipart/form-data", follow_redirects=True)
    assert resp.status_code == 200
    from app.modules.maintenance_config.models import PMSchedule
    assert PMSchedule.query.count() == 1


def test_scope_import_page_renders(client, db):
    _login(client, db, codes=["pmscopetemplate.view", "pmscopetemplate.create"])
    assert client.get("/admin/pm-scope-templates/import").status_code == 200


def test_scope_import_uploads_csv(client, db):
    _login(client, db, codes=["pmscopetemplate.view", "pmscopetemplate.create"])
    MaintenanceTypeService().create(code="PMS-IMP2", name="PMS Import Test2",
                                    category="PREVENTIVE")
    csv_content = (
        "maintenance_type_code,scope_template_name,activity_code,"
        "activity_description,standard_labor_hours,estimated_cost,"
        "required_parts,vendor_recommendation,sort_order\n"
        "PMS-IMP2,Scope Test,OIL,Change Oil,0.5,500,Oil,Vendor,1\n")
    resp = client.post("/admin/pm-scope-templates/import", data={
        "csv_file": (io.BytesIO(csv_content.encode()), "scope.csv"),
    }, content_type="multipart/form-data", follow_redirects=True)
    assert resp.status_code == 200
    from app.modules.maintenance_config.models import PMScopeTemplate
    assert PMScopeTemplate.query.filter_by(name="Scope Test").count() == 1
