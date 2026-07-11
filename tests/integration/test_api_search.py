from datetime import date

from app.core.security.password import hash_password
from app.modules.user_management.models import User, Role, Permission
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.driver.service import DriverService
from app.modules.master_data.vendor.service import VendorService


def _login(client, db, *, codes=()):
    role = Role(name="SearchRole")
    for code in codes:
        m, a = code.split(".")
        p = Permission(code=code, module=m, action=a)
        db.session.add(p)
        role.permissions.append(p)
    u = User(username="sana", email="sana@x.com",
             password_hash=hash_password("pw123456"))
    u.roles.append(role)
    db.session.add_all([role, u])
    db.session.commit()
    client.post("/login", data={"username": "sana", "password": "pw123456"})
    return u


def test_vehicle_search_requires_permission(client, db):
    _login(client, db)
    resp = client.get("/api/search/vehicles?q=ABC")
    assert resp.status_code == 403


def test_vehicle_search_returns_select2_shape(client, db):
    _login(client, db, codes=["vehicle.view"])
    branch = BranchService().create(code="BR-API", name="API Branch")
    vt = VehicleTypeService().create(code="LV-API", name="Light", category="LIGHT")
    VehicleService().create(vehicle_type_id=vt.id, brand="Toyota", model="Hilux",
                            year=2024, branch_id=branch.id,
                            conduction_number="API-001")
    resp = client.get("/api/search/vehicles?q=API-001")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "results" in data and "pagination" in data
    assert data["results"][0]["text"].startswith("API-001")


def test_driver_search(client, db):
    _login(client, db, codes=["driver.view"])
    branch = BranchService().create(code="BR-API2", name="API Branch 2")
    DriverService().create(employee_number="EMP-API1", first_name="Juan",
                           last_name="Cruz", license_number="LIC-API1",
                           license_expiry=date(2030, 1, 1),
                           license_type="PROFESSIONAL", branch_id=branch.id)
    resp = client.get("/api/search/drivers?q=Cruz")
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data["results"]) == 1
    assert "Cruz" in data["results"][0]["text"]


def test_user_search(client, db):
    _login(client, db, codes=["user.view"])
    resp = client.get("/api/search/users?q=sana")
    assert resp.status_code == 200
    data = resp.get_json()
    assert any("sana" in r["text"] for r in data["results"])


def test_vendor_search(client, db):
    _login(client, db, codes=["vendor.view"])
    VendorService().create(code="VEN-API", name="API Auto Parts")
    resp = client.get("/api/search/vendors?q=API Auto")
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data["results"]) == 1


def test_search_pagination_params(client, db):
    _login(client, db, codes=["vehicle.view"])
    branch = BranchService().create(code="BR-API3", name="API Branch 3")
    vt = VehicleTypeService().create(code="LV-API3", name="Light", category="LIGHT")
    for i in range(15):
        VehicleService().create(vehicle_type_id=vt.id, brand="Toyota",
                                model="Vios", year=2024, branch_id=branch.id,
                                conduction_number=f"PG-{i:03d}")
    resp = client.get("/api/search/vehicles?q=PG&page=1&per_page=10")
    data = resp.get_json()
    assert len(data["results"]) == 10
    assert data["pagination"]["more"] is True
