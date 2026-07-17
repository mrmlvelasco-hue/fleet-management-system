from app.core.security.password import hash_password
from app.modules.user_management.models import User, Role, Permission
from app.modules.master_data.reference.service import MaintenanceTypeService
from app.modules.maintenance_config.service import PMScopeTemplateService


def _login(client, db, *, codes=()):
    role = Role(name="ScopeDetailsUIRole")
    for code in codes:
        m, a = code.split(".")
        p = Permission.query.filter_by(code=code).first()
        if p is None:
            p = Permission(code=code, module=m, action=a)
            db.session.add(p)
        role.permissions.append(p)
    u = User(username="scope_details_ui_user", email="scope_details_ui_user@x.com",
             password_hash=hash_password("pw123456"))
    u.roles.append(role)
    db.session.add_all([role, u])
    db.session.commit()
    client.post("/login", data={"username": "scope_details_ui_user", "password": "pw123456"})
    return u


def test_scope_template_details_endpoint(client, db):
    _login(client, db, codes=["maintenanceorder.view"])
    mt = MaintenanceTypeService().create(code="SCOPEDETAILS-MT", name="Scope Details Test",
                                         category="PM")
    tmpl = PMScopeTemplateService().create(
        maintenance_type_id=mt.id, name="Scope Details Test Checklist",
        items=[{"activity_code": "S02-00001-001",
               "activity_description": "Perform first 1,000 km PMS except change of oil and oil filter.",
               "sort_order": 1}])
    resp = client.get(f"/api/search/pm-scope-template-details/{tmpl.id}")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["found"] is True
    assert data["items"][0]["activity_code"] == "S02-00001-001"
    assert "Perform first 1,000 km" in data["items"][0]["activity_description"]


def test_scope_template_details_not_found(client, db):
    _login(client, db, codes=["maintenanceorder.view"])
    resp = client.get("/api/search/pm-scope-template-details/999999")
    assert resp.status_code == 200
    assert resp.get_json()["found"] is False


def test_mo_form_has_collapsed_scope_details_panel(client, db):
    """Confirms the panel exists and starts collapsed (no 'show' class),
    matching the established pattern from the PM Template detail page."""
    _login(client, db, codes=["maintenanceorder.view", "maintenanceorder.create"])
    resp = client.get("/transactions/maintenance-orders/new")
    assert resp.status_code == 200
    assert b"View Scope Details" in resp.data
    assert b'id="moScopeDetailsCollapse"' in resp.data
    assert b'class="collapse mt-2" id="moScopeDetailsCollapse"' in resp.data


def test_registration_template_details_for_vehicle_endpoint(client, db):
    from datetime import date
    from app.modules.master_data.vehicle.service import VehicleService
    from app.modules.master_data.reference.service import VehicleTypeService
    from app.modules.master_data.org.service import BranchService
    from app.modules.registration_config.service import RegistrationTemplateService

    _login(client, db, codes=["vehicleregistration.view"])
    branch = BranchService().create(code="BR-REGCHECKLISTUI", name="Reg Checklist UI Branch")
    vt = VehicleTypeService().create(code="LV-REGCHECKLISTUI", name="Light", category="LIGHT")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2024,
        branch_id=branch.id, conduction_number="REGCHECKLISTUI-000")
    RegistrationTemplateService().create(
        vehicle_type_id=vt.id, interval_years=3,
        items=[{"activity_code": "OR-CR", "activity_description": "Renew OR/CR",
               "sort_order": 1}])

    resp = client.get(
        f"/api/search/registration-template-details-for-vehicle?vehicle_id={vehicle.id}")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["found"] is True
    assert data["items"][0]["activity_code"] == "OR-CR"


def test_registration_form_has_collapsed_checklist_panel(client, db):
    _login(client, db, codes=["vehicleregistration.view", "vehicleregistration.create"])
    resp = client.get("/transactions/vehicle-registrations/new")
    assert resp.status_code == 200
    assert b"View Checklist Details" in resp.data
    assert b'id="vrChecklistCollapse"' in resp.data
    assert b'class="collapse mt-2" id="vrChecklistCollapse"' in resp.data
