from app.core.security.password import hash_password
from app.modules.user_management.models import User, Role, Permission


def _login(client, db, *, codes=()):
    role = Role(name="ModalWireRole")
    for code in codes:
        m, a = code.split(".")
        p = Permission(code=code, module=m, action=a)
        db.session.add(p)
        role.permissions.append(p)
    u = User(username="dania", email="dania@x.com",
             password_hash=hash_password("pw123456"))
    u.roles.append(role)
    db.session.add_all([role, u])
    db.session.commit()
    client.post("/login", data={"username": "dania", "password": "pw123456"})
    return u


def test_search_modal_shell_present_on_dashboard(client, db):
    _login(client, db)
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"fmsSearchModal" in resp.data


def test_tripticket_form_wires_vehicle_search_modal(client, db):
    _login(client, db, codes=["tripticket.view", "tripticket.create"])
    resp = client.get("/transactions/trip-tickets/new")
    assert resp.status_code == 200
    assert b"ttVehicleModalBtn" in resp.data
    assert b"wireVehicleSearchModal" in resp.data
    assert b"/api/search/vehicles/table" in resp.data


def test_atd_form_wires_vehicle_search_modal(client, db):
    _login(client, db, codes=["atd.view", "atd.create"])
    resp = client.get("/transactions/atd/new")
    assert resp.status_code == 200
    assert b"atdVehicleModalBtn" in resp.data


def test_vehiclemovement_form_wires_search_modal(client, db):
    _login(client, db, codes=["vehiclemovement.view", "vehiclemovement.create"])
    resp = client.get("/transactions/vehicle-movements/new")
    assert resp.status_code == 200
    assert b"vmVehicleModalBtn" in resp.data


def test_maintenanceorder_form_wires_search_modal(client, db):
    _login(client, db, codes=["maintenanceorder.view", "maintenanceorder.create"])
    resp = client.get("/transactions/maintenance-orders/new")
    assert resp.status_code == 200
    assert b"moVehicleModalBtn" in resp.data


def test_tiretxn_form_wires_search_modal(client, db):
    _login(client, db, codes=["tiretxn.view", "tiretxn.create"])
    resp = client.get("/transactions/tire-transactions/new")
    assert resp.status_code == 200
    assert b"tirtxVehicleModalBtn" in resp.data


def test_batterytxn_form_wires_search_modal(client, db):
    _login(client, db, codes=["batterytxn.view", "batterytxn.create"])
    resp = client.get("/transactions/battery-transactions/new")
    assert resp.status_code == 200
    assert b"battxVehicleModalBtn" in resp.data
