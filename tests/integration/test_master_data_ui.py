from app.core.security.password import hash_password
from app.modules.user_management.models import User, Role, Permission


def _login(client, db, *, codes=()):
    role = Role(name="MasterDataRole")
    for code in codes:
        m, a = code.split(".")
        p = Permission(code=code, module=m, action=a)
        db.session.add(p)
        role.permissions.append(p)
    u = User(username="leo", email="l@x.com",
             password_hash=hash_password("pw123456"))
    u.roles.append(role)
    db.session.add_all([role, u])
    db.session.commit()
    client.post("/login", data={"username": "leo", "password": "pw123456"})
    return u


def test_vehicle_list_403_without_permission(client, db):
    _login(client, db)
    assert client.get("/master/vehicles").status_code == 403


def test_vehicle_list_200_with_permission(client, db):
    _login(client, db, codes=["vehicle.view"])
    assert client.get("/master/vehicles").status_code == 200


def test_driver_list_renders(client, db):
    _login(client, db, codes=["driver.view"])
    assert client.get("/master/drivers").status_code == 200


def test_branch_list_renders(client, db):
    _login(client, db, codes=["branch.view"])
    assert client.get("/master/branches").status_code == 200


def test_vendor_list_renders(client, db):
    _login(client, db, codes=["vendor.view"])
    assert client.get("/master/vendors").status_code == 200


def test_tire_list_renders(client, db):
    _login(client, db, codes=["tire.view"])
    assert client.get("/master/tires").status_code == 200


def test_battery_list_renders(client, db):
    _login(client, db, codes=["battery.view"])
    assert client.get("/master/batteries").status_code == 200
