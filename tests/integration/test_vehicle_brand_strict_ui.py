from app.core.security.password import hash_password
from app.modules.user_management.models import User, Role, Permission
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.vehicle_brand.service import (
    VehicleBrandService, VehicleModelService)


def _login(client, db, *, codes=()):
    role = Role(name="StrictBrandRole")
    for code in codes:
        m, a = code.split(".")
        p = Permission(code=code, module=m, action=a)
        db.session.add(p)
        role.permissions.append(p)
    u = User(username="hafsa", email="hafsa@x.com",
             password_hash=hash_password("pw123456"))
    u.roles.append(role)
    db.session.add_all([role, u])
    db.session.commit()
    client.post("/login", data={"username": "hafsa", "password": "pw123456"})
    return u


def _seed(db):
    branch = BranchService().create(code="BR-STRICT", name="Strict Branch")
    vt = VehicleTypeService().create(code="LV-STRICT", name="Light", category="LIGHT")
    return branch, vt


def test_vehicle_form_shows_brand_and_model_selects(client, db):
    _login(client, db, codes=["vehicle.view", "vehicle.create"])
    resp = client.get("/master/vehicles/new")
    assert resp.status_code == 200
    assert b'id="vehBrandSelect"' in resp.data
    assert b'id="vehModelSelect"' in resp.data


def test_creating_vehicle_with_unregistered_brand_shows_friendly_error(client, db):
    """The core ask: typing/submitting a Brand that isn't in the master
    list must be rejected with a friendly message, not silently accepted."""
    _login(client, db, codes=["vehicle.view", "vehicle.create"])
    branch, vt = _seed(db)
    resp = client.post("/master/vehicles/new", data={
        "vehicle_type_id": str(vt.id), "brand": "NotARealBrand",
        "model": "SomeModel", "year": "2024", "branch_id": str(branch.id),
        "conduction_number": "STRICT-001",
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert b"Please select a valid Brand from the master list" in resp.data
    from app.modules.master_data.vehicle.models import Vehicle
    assert Vehicle.query.filter_by(conduction_number="STRICT-001").count() == 0


def test_creating_vehicle_with_model_not_belonging_to_brand_shows_friendly_error(client, db):
    _login(client, db, codes=["vehicle.view", "vehicle.create"])
    branch, vt = _seed(db)
    toyota = VehicleBrandService().create(name="Toyota")
    honda = VehicleBrandService().create(name="Honda")
    VehicleModelService().create(brand_id=honda.id, name="City")

    resp = client.post("/master/vehicles/new", data={
        "vehicle_type_id": str(vt.id), "brand": "Toyota", "model": "City",
        "year": "2024", "branch_id": str(branch.id),
        "conduction_number": "STRICT-002",
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert b"Selected Model does not belong to the selected Brand" in resp.data


def test_creating_vehicle_with_valid_brand_and_model_succeeds(client, db):
    _login(client, db, codes=["vehicle.view", "vehicle.create"])
    branch, vt = _seed(db)
    toyota = VehicleBrandService().create(name="Toyota")
    VehicleModelService().create(brand_id=toyota.id, name="Hilux")

    resp = client.post("/master/vehicles/new", data={
        "vehicle_type_id": str(vt.id), "brand": "Toyota", "model": "Hilux",
        "year": "2024", "branch_id": str(branch.id),
        "conduction_number": "STRICT-003",
    }, follow_redirects=True)
    assert resp.status_code == 200
    from app.modules.master_data.vehicle.models import Vehicle
    vehicle = Vehicle.query.filter_by(conduction_number="STRICT-003").first()
    assert vehicle is not None
    assert vehicle.brand == "Toyota"
    assert vehicle.model == "Hilux"


def test_missing_brand_shows_required_error(client, db):
    _login(client, db, codes=["vehicle.view", "vehicle.create"])
    branch, vt = _seed(db)
    resp = client.post("/master/vehicles/new", data={
        "vehicle_type_id": str(vt.id), "brand": "", "model": "Something",
        "year": "2024", "branch_id": str(branch.id),
        "conduction_number": "STRICT-004",
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert b"Brand is required" in resp.data


def test_vehiclemodel_endpoint_filters_by_brand(client, db):
    _login(client, db, codes=["vehicle.view"])
    toyota = VehicleBrandService().create(name="Toyota-Filter")
    honda = VehicleBrandService().create(name="Honda-Filter")
    VehicleModelService().create(brand_id=toyota.id, name="Hilux-F")
    VehicleModelService().create(brand_id=honda.id, name="City-F")

    resp = client.get(f"/api/search/vehicle-models?brand_id={toyota.id}")
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data["results"]) == 1
    assert data["results"][0]["name"] == "Hilux-F"
