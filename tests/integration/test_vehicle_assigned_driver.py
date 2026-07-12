from datetime import date

from app.core.security.password import hash_password
from app.modules.user_management.models import User, Role, Permission
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.vehicle_brand.service import (
    VehicleBrandService, VehicleModelService)
from app.modules.master_data.driver.service import DriverService


def _login(client, db, *, codes=()):
    role = Role(name="AssignedDriverRole")
    for code in codes:
        m, a = code.split(".")
        p = Permission(code=code, module=m, action=a)
        db.session.add(p)
        role.permissions.append(p)
    u = User(username="karim", email="karim@x.com",
             password_hash=hash_password("pw123456"))
    u.roles.append(role)
    db.session.add_all([role, u])
    db.session.commit()
    client.post("/login", data={"username": "karim", "password": "pw123456"})
    return u


def test_vehicle_form_shows_assigned_driver_field(client, db):
    _login(client, db, codes=["vehicle.view", "vehicle.create"])
    resp = client.get("/master/vehicles/new")
    assert resp.status_code == 200
    assert b'id="vehDriverSelect"' in resp.data
    assert b"/api/search/drivers" in resp.data


def test_create_vehicle_with_assigned_driver(client, db):
    _login(client, db, codes=["vehicle.view", "vehicle.create"])
    branch = BranchService().create(code="BR-ASSIGN", name="Assign Branch")
    vt = VehicleTypeService().create(code="LV-ASSIGN", name="Light", category="LIGHT")
    toyota = VehicleBrandService().create(name="ToyotaAssign")
    VehicleModelService().create(brand_id=toyota.id, name="ViosAssign")
    driver = DriverService().create(
        employee_number="EMP-ASSIGN1", first_name="Liza", last_name="Reyes",
        license_number="LIC-ASSIGN1", license_expiry=date(2030, 1, 1),
        license_type="PROFESSIONAL", branch_id=branch.id)

    resp = client.post("/master/vehicles/new", data={
        "vehicle_type_id": str(vt.id), "brand": "ToyotaAssign",
        "model": "ViosAssign", "year": "2024", "branch_id": str(branch.id),
        "conduction_number": "ASSIGN-001",
        "assigned_driver_id": str(driver.id),
    }, follow_redirects=True)
    assert resp.status_code == 200

    from app.modules.master_data.vehicle.models import Vehicle
    vehicle = Vehicle.query.filter_by(conduction_number="ASSIGN-001").first()
    assert vehicle is not None
    assert vehicle.assigned_driver_id == driver.id
    assert vehicle.assigned_driver.last_name == "Reyes"


def test_vehicle_detail_shows_assigned_driver(client, db):
    user = _login(client, db, codes=["vehicle.view", "vehicle.create"])
    branch = BranchService().create(code="BR-ASSIGN2", name="Assign Branch 2")
    vt = VehicleTypeService().create(code="LV-ASSIGN2", name="Light", category="LIGHT")
    driver = DriverService().create(
        employee_number="EMP-ASSIGN2", first_name="Tomas", last_name="Cruz",
        license_number="LIC-ASSIGN2", license_expiry=date(2030, 1, 1),
        license_type="PROFESSIONAL", branch_id=branch.id)
    from app.modules.master_data.vehicle.service import VehicleService
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Honda", model="City", year=2024,
        branch_id=branch.id, conduction_number="ASSIGN-002",
        assigned_driver_id=driver.id)

    resp = client.get(f"/master/vehicles/{vehicle.id}")
    assert resp.status_code == 200
    assert b"Cruz" in resp.data
    assert b"EMP-ASSIGN2" in resp.data


def test_only_active_drivers_appear_in_search(client, db):
    _login(client, db, codes=["vehicle.view"])
    branch = BranchService().create(code="BR-ASSIGN3", name="Assign Branch 3")
    active_driver = DriverService().create(
        employee_number="EMP-ACTIVE", first_name="Ana", last_name="Santos",
        license_number="LIC-ACTIVE", license_expiry=date(2030, 1, 1),
        license_type="PROFESSIONAL", branch_id=branch.id)
    inactive_driver = DriverService().create(
        employee_number="EMP-INACTIVE", first_name="Boy", last_name="Garcia",
        license_number="LIC-INACTIVE", license_expiry=date(2030, 1, 1),
        license_type="PROFESSIONAL", branch_id=branch.id)
    inactive_driver.is_active = False
    from app.extensions import db as _db
    _db.session.commit()

    resp = client.get("/api/search/drivers?q=EMP-")
    data = resp.get_json()
    names = [r["text"] for r in data["results"]]
    assert any("Santos" in n for n in names)
    assert not any("Garcia" in n for n in names)
