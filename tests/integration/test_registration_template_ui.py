from app.core.security.password import hash_password
from app.modules.user_management.models import User, Role, Permission
from app.modules.master_data.reference.service import VehicleTypeService


def _login(client, db, *, codes=()):
    role = Role(name="RegTemplateUIRole")
    for code in codes:
        m, a = code.split(".")
        p = Permission.query.filter_by(code=code).first()
        if p is None:
            p = Permission(code=code, module=m, action=a)
            db.session.add(p)
        role.permissions.append(p)
    u = User(username="regtemplate_ui_user", email="regtemplate_ui_user@x.com",
             password_hash=hash_password("pw123456"))
    u.roles.append(role)
    db.session.add_all([role, u])
    db.session.commit()
    client.post("/login", data={"username": "regtemplate_ui_user", "password": "pw123456"})
    return u


def test_registration_template_list_renders(client, db):
    _login(client, db, codes=["registrationtemplate.view"])
    resp = client.get("/admin/registration-templates")
    assert resp.status_code == 200
    assert b"Registration Templates" in resp.data


def test_create_registration_template_with_checklist(client, db):
    _login(client, db, codes=["registrationtemplate.view", "registrationtemplate.create"])
    vt = VehicleTypeService().create(code="LV-REGUI", name="Light", category="LIGHT")
    resp = client.post("/admin/registration-templates/new", data={
        "vehicle_type_id": str(vt.id), "interval_years": "3",
        "next_generation_policy": "AUTO_SCHEDULE", "priority": "MEDIUM",
        "activity_code": ["OR-CR", "EMISSION"],
        "activity_description": ["Renew OR/CR", "Emission Test"],
    }, follow_redirects=True)
    assert resp.status_code == 200

    from app.modules.registration_config.models import RegistrationTemplate
    tmpl = RegistrationTemplate.query.filter_by(vehicle_type_id=vt.id).first()
    assert tmpl is not None
    assert len(tmpl.checklist_items) == 2


def test_registration_template_detail_and_edit(client, db):
    _login(client, db, codes=["registrationtemplate.view", "registrationtemplate.create",
                              "registrationtemplate.update"])
    vt = VehicleTypeService().create(code="LV-REGUI2", name="Light", category="LIGHT")
    from app.modules.registration_config.service import RegistrationTemplateService
    tmpl = RegistrationTemplateService().create(
        vehicle_type_id=vt.id, interval_years=3,
        items=[{"activity_code": "OR-CR", "activity_description": "Renew OR/CR",
               "sort_order": 1}])

    detail_resp = client.get(f"/admin/registration-templates/{tmpl.id}")
    assert detail_resp.status_code == 200
    assert b"Renew OR/CR" in detail_resp.data

    edit_resp = client.get(f"/admin/registration-templates/{tmpl.id}/edit")
    assert edit_resp.status_code == 200
    assert b"Renew OR/CR" in edit_resp.data


def test_sidebar_shows_registration_templates_link(client, db):
    _login(client, db, codes=["registrationtemplate.view"])
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"Registration Templates" in resp.data
