from app.core.security.password import hash_password
from app.modules.user_management.models import User, Role, Permission
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.driver.service import DriverService
from app.modules.master_data.vendor.service import VendorService


def _login(client, db, *, codes=()):
    role = Role(name="RegressionRole")
    for code in codes:
        m, a = code.split(".")
        p = Permission(code=code, module=m, action=a)
        db.session.add(p)
        role.permissions.append(p)
    u = User(username="mia", email="mia@x.com",
             password_hash=hash_password("pw123456"))
    u.roles.append(role)
    db.session.add_all([role, u])
    db.session.commit()
    client.post("/login", data={"username": "mia", "password": "pw123456"})
    return u


def test_vehicletype_new_form_renders(client, db):
    """Regression: broken {{ Vehicle Types }} Jinja2 expression."""
    _login(client, db, codes=["vehicletype.view", "vehicletype.create"])
    resp = client.get("/master/vehicle-types/new")
    assert resp.status_code == 200
    assert b"Vehicle Types" in resp.data


def test_vendor_form_includes_vendor_type_field(client, db):
    """Regression: vendor_type field missing from form."""
    _login(client, db, codes=["vendor.view", "vendor.create"])
    resp = client.get("/master/vendors/new")
    assert resp.status_code == 200
    assert b'name="vendor_type"' in resp.data
    assert b"GOODS" in resp.data


def test_driver_list_renders_with_today(client, db):
    """Regression: 'today' undefined in driver_list.html."""
    _login(client, db, codes=["driver.view"])
    resp = client.get("/master/drivers")
    assert resp.status_code == 200


def test_driver_detail_renders(client, db):
    """Regression: template used 'driver' but route passed 'item'."""
    _login(client, db, codes=["driver.view", "driver.create"])
    driver = DriverService().create(
        employee_number="EMP-100", first_name="Test", last_name="Driver",
        license_number="LIC-100",
        license_expiry=__import__("datetime").date(2030, 1, 1),
        license_type="PROFESSIONAL",
        branch_id=BranchService().create(code="BR1", name="Branch 1").id)
    resp = client.get(f"/master/drivers/{driver.id}")
    assert resp.status_code == 200
    assert b"Test" in resp.data


def test_driver_form_includes_license_type_select(client, db):
    """Regression: license_types not passed to driver form."""
    _login(client, db, codes=["driver.view", "driver.create"])
    resp = client.get("/master/drivers/new")
    assert resp.status_code == 200
    assert b'name="license_type"' in resp.data


def test_vehicle_detail_renders(client, db):
    """Regression: template used 'vehicle' but route passed 'item'."""
    from app.modules.master_data.vehicle.service import VehicleService
    from app.modules.master_data.reference.service import VehicleTypeService
    _login(client, db, codes=["vehicle.view", "vehicle.create"])
    vt = VehicleTypeService().create(code="LV", name="Light Vehicle",
                                     category="LIGHT")
    branch = BranchService().create(code="BR2", name="Branch 2")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2024,
        branch_id=branch.id, conduction_number="ABC-123")
    resp = client.get(f"/master/vehicles/{vehicle.id}")
    assert resp.status_code == 200
    assert b"Toyota" in resp.data


def test_vehicle_form_includes_fuel_type_select(client, db):
    """Regression: fuel_types not passed to vehicle form."""
    _login(client, db, codes=["vehicle.view", "vehicle.create"])
    resp = client.get("/master/vehicles/new")
    assert resp.status_code == 200
    assert b'name="fuel_type"' in resp.data
