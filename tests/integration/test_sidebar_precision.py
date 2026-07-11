from app.core.security.password import hash_password
from app.modules.user_management.models import User, Role, Permission


def _login(client, db, *, codes=()):
    role = Role(name="SidebarPrecisionRole")
    for code in codes:
        m, a = code.split(".")
        p = Permission(code=code, module=m, action=a)
        db.session.add(p)
        role.permissions.append(p)
    u = User(username="yasmin", email="yasmin@x.com",
             password_hash=hash_password("pw123456"))
    u.roles.append(role)
    db.session.add_all([role, u])
    db.session.commit()
    client.post("/login", data={"username": "yasmin", "password": "pw123456"})
    return u


def test_vehicletype_page_does_not_highlight_vehicles_link(client, db):
    """Regression: Vehicle Types page was also highlighting the Vehicles
    link because 'vehicle' is a substring of 'vehicletype'."""
    _login(client, db, codes=["vehicletype.view", "vehicle.view"])
    resp = client.get("/master/vehicle-types")
    assert resp.status_code == 200
    html = resp.data.decode()

    import re
    vehicles_link = re.search(
        r'<a href="/master/vehicles"[^>]*class="[^"]*"', html)
    vehicletypes_link = re.search(
        r'<a href="/master/vehicle-types"[^>]*class="[^"]*"', html)
    assert vehicles_link is not None
    assert vehicletypes_link is not None
    assert "active" not in vehicles_link.group()
    assert "active" in vehicletypes_link.group()


def test_tiretxn_page_does_not_highlight_tires_master_link(client, db):
    _login(client, db, codes=["tiretxn.view", "tire.view"])
    resp = client.get("/transactions/tire-transactions")
    assert resp.status_code == 200
    html = resp.data.decode()

    import re
    tires_link = re.search(r'<a href="/master/tires"[^>]*class="[^"]*"', html)
    tiretxn_link = re.search(
        r'<a href="/transactions/tire-transactions"[^>]*class="[^"]*"', html)
    assert tires_link is not None
    assert tiretxn_link is not None
    assert "active" not in tires_link.group()
    assert "active" in tiretxn_link.group()


def test_batterytxn_page_does_not_highlight_batteries_master_link(client, db):
    _login(client, db, codes=["batterytxn.view", "battery.view"])
    resp = client.get("/transactions/battery-transactions")
    assert resp.status_code == 200
    html = resp.data.decode()

    import re
    batteries_link = re.search(
        r'<a href="/master/batteries"[^>]*class="[^"]*"', html)
    batterytxn_link = re.search(
        r'<a href="/transactions/battery-transactions"[^>]*class="[^"]*"', html)
    assert batteries_link is not None
    assert batterytxn_link is not None
    assert "active" not in batteries_link.group()
    assert "active" in batterytxn_link.group()


def test_vehiclemovement_page_does_not_highlight_vehicles_master_link(client, db):
    _login(client, db, codes=["vehiclemovement.view", "vehicle.view"])
    resp = client.get("/transactions/vehicle-movements")
    assert resp.status_code == 200
    html = resp.data.decode()

    import re
    vehicles_link = re.search(
        r'<a href="/master/vehicles"[^>]*class="[^"]*"', html)
    movement_link = re.search(
        r'<a href="/transactions/vehicle-movements"[^>]*class="[^"]*"', html)
    assert vehicles_link is not None
    assert movement_link is not None
    assert "active" not in vehicles_link.group()
    assert "active" in movement_link.group()
