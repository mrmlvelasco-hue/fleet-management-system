from datetime import date

from app.core.security.password import hash_password
from app.modules.user_management.models import User, Role, Permission
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.vehicle_brand.service import (
    VehicleBrandService, VehicleModelService)


def _login(client, db, *, codes=()):
    role = Role(name="VMEnhUIRole")
    for code in codes:
        m, a = code.split(".")
        p = Permission.query.filter_by(code=code).first()
        if p is None:
            p = Permission(code=code, module=m, action=a)
            db.session.add(p)
        role.permissions.append(p)
    u = User(username="vmenh_ui_user", email="vmenh_ui_user@x.com",
             password_hash=hash_password("pw123456"))
    u.roles.append(role)
    db.session.add_all([role, u])
    db.session.commit()
    client.post("/login", data={"username": "vmenh_ui_user", "password": "pw123456"})
    return u


def test_vehicle_form_has_new_field_sections(client, db):
    _login(client, db, codes=["vehicle.view", "vehicle.create"])
    resp = client.get("/master/vehicles/new")
    assert resp.status_code == 200
    assert b"Identifiers" in resp.data
    assert b"Classification" in resp.data
    assert b"Financials" in resp.data
    assert b"Insurance" in resp.data
    assert b'name="far_number"' in resp.data
    assert b'name="ctpl_from_date"' in resp.data
    assert b'name="assignment"' in resp.data
    assert b">Clone<" not in resp.data  # Clone only shows for an existing vehicle


def test_create_vehicle_with_full_enhancement_data(client, db):
    _login(client, db, codes=["vehicle.view", "vehicle.create"])
    branch = BranchService().create(code="BR-VMENHUI", name="VM Enh UI Branch")
    vt = VehicleTypeService().create(code="LV-VMENHUI", name="Light", category="LIGHT")
    toyota = VehicleBrandService().create(name="Toyota-VMENHUI")
    VehicleModelService().create(brand_id=toyota.id, name="Hilux-VMENHUI")

    resp = client.post("/master/vehicles/new", data={
        "vehicle_type_id": str(vt.id), "brand": "Toyota-VMENHUI",
        "model": "Hilux-VMENHUI", "year": "2024", "branch_id": str(branch.id),
        "conduction_number": "VMENHUI-000",
        "far_number": "FAR-UI-1", "vehicle_body_type": "PICKUP",
        "assignment": "PRIMARY", "assignment_group_classification": "COMPANY_OWNED",
        "vehicle_usage": "NON_SALES", "mr_eds": "NO",
        "with_vehicle_contract": "NO", "has_ctpl": "on",
        "ctpl_from_date": "2024-01-01", "ctpl_to_date": "2025-01-01",
    }, follow_redirects=True)
    assert resp.status_code == 200

    from app.modules.master_data.vehicle.models import Vehicle
    vehicle = Vehicle.query.filter_by(conduction_number="VMENHUI-000").first()
    assert vehicle is not None
    assert vehicle.far_number == "FAR-UI-1"
    assert vehicle.assignment == "PRIMARY"
    assert vehicle.has_ctpl is True


def test_invalid_acquisition_cost_shows_friendly_error(client, db):
    _login(client, db, codes=["vehicle.view", "vehicle.create"])
    branch = BranchService().create(code="BR-VMENHUI2", name="VM Enh UI Branch 2")
    vt = VehicleTypeService().create(code="LV-VMENHUI2", name="Light", category="LIGHT")
    toyota = VehicleBrandService().create(name="Toyota-VMENHUI2")
    VehicleModelService().create(brand_id=toyota.id, name="Hilux-VMENHUI2")

    resp = client.post("/master/vehicles/new", data={
        "vehicle_type_id": str(vt.id), "brand": "Toyota-VMENHUI2",
        "model": "Hilux-VMENHUI2", "year": "2024", "branch_id": str(branch.id),
        "conduction_number": "VMENHUI2-000", "acquisition_cost": "-500",
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert b"must be greater than zero" in resp.data


def test_clone_button_appears_on_edit_and_prefills_form(client, db):
    _login(client, db, codes=["vehicle.view", "vehicle.create", "vehicle.update"])
    branch = BranchService().create(code="BR-VMENHUI3", name="VM Enh UI Branch 3")
    vt = VehicleTypeService().create(code="LV-VMENHUI3", name="Light", category="LIGHT")
    toyota = VehicleBrandService().create(name="Toyota-VMENHUI3")
    VehicleModelService().create(brand_id=toyota.id, name="Hilux-VMENHUI3")

    from app.modules.master_data.vehicle.service import VehicleService
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota-VMENHUI3", model="Hilux-VMENHUI3",
        year=2024, branch_id=branch.id, conduction_number="VMENHUI3-000",
        plate_number="CLONE-UI-1", color="Blue", supplier="Test Supplier")

    edit_resp = client.get(f"/master/vehicles/{vehicle.id}/edit")
    assert b">Clone<" in edit_resp.data

    clone_resp = client.get(f"/master/vehicles/{vehicle.id}/clone")
    assert clone_resp.status_code == 200
    assert b'value="Blue"' in clone_resp.data  # color carried over
    assert b'value="Test Supplier"' in clone_resp.data  # supplier carried over
    assert b"CLONE-UI-1" not in clone_resp.data  # plate number NOT carried over
