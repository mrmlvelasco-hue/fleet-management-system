from app.core.security.password import hash_password
from app.modules.user_management.models import User, Role, Permission


def _login(client, db, *, codes=()):
    role = Role(name="SmartSelectRole")
    for code in codes:
        m, a = code.split(".")
        p = Permission(code=code, module=m, action=a)
        db.session.add(p)
        role.permissions.append(p)
    u = User(username="leila", email="leila@x.com",
             password_hash=hash_password("pw123456"))
    u.roles.append(role)
    db.session.add_all([role, u])
    db.session.commit()
    client.post("/login", data={"username": "leila", "password": "pw123456"})
    return u


def test_tripticket_form_uses_ajax_vehicle_and_driver_select(client, db):
    _login(client, db, codes=["tripticket.view", "tripticket.create"])
    resp = client.get("/transactions/trip-tickets/new")
    assert resp.status_code == 200
    assert b"ttVehicleSelect" in resp.data
    assert b"ttDriverSelect" in resp.data
    assert b"/api/search/vehicles" in resp.data
    assert b"/api/search/drivers" in resp.data


def test_atd_form_uses_ajax_selects(client, db):
    _login(client, db, codes=["atd.view", "atd.create"])
    resp = client.get("/transactions/atd/new")
    assert resp.status_code == 200
    assert b"atdVehicleSelect" in resp.data
    assert b"atdDriverSelect" in resp.data


def test_vehiclemovement_form_uses_ajax_vehicle_select(client, db):
    _login(client, db, codes=["vehiclemovement.view", "vehiclemovement.create"])
    resp = client.get("/transactions/vehicle-movements/new")
    assert resp.status_code == 200
    assert b"vmVehicleSelect" in resp.data


def test_maintenanceorder_form_uses_ajax_vehicle_and_vendor_select(client, db):
    _login(client, db, codes=["maintenanceorder.view", "maintenanceorder.create"])
    resp = client.get("/transactions/maintenance-orders/new")
    assert resp.status_code == 200
    assert b"moVehicleSelect" in resp.data
    assert b"moVendorSelect" in resp.data


def test_tiretxn_form_uses_ajax_vehicle_select(client, db):
    _login(client, db, codes=["tiretxn.view", "tiretxn.create"])
    resp = client.get("/transactions/tire-transactions/new")
    assert resp.status_code == 200
    assert b"tirtxVehicleSelect" in resp.data


def test_batterytxn_form_uses_ajax_vehicle_select(client, db):
    _login(client, db, codes=["batterytxn.view", "batterytxn.create"])
    resp = client.get("/transactions/battery-transactions/new")
    assert resp.status_code == 200
    assert b"battxVehicleSelect" in resp.data


def test_purchaserequest_form_uses_ajax_vendor_select(client, db):
    _login(client, db, codes=["purchaserequest.view", "purchaserequest.create"])
    resp = client.get("/transactions/purchase-requests/new")
    assert resp.status_code == 200
    assert b"prVendorSelect" in resp.data


def test_tire_form_uses_ajax_vendor_select(client, db):
    _login(client, db, codes=["tire.view", "tire.create"])
    resp = client.get("/master/tires/new")
    assert resp.status_code == 200
    assert b"tireVendorSelect" in resp.data


def test_battery_form_uses_ajax_vendor_select(client, db):
    _login(client, db, codes=["battery.view", "battery.create"])
    resp = client.get("/master/batteries/new")
    assert resp.status_code == 200
    assert b"battVendorSelect" in resp.data


def test_tripticket_full_flow_still_works_with_ajax_selects(client, db):
    """End-to-end: form submission (not the widget itself) still works
    correctly now that the <select> no longer preloads options."""
    from datetime import date
    from app.modules.master_data.org.service import BranchService
    from app.modules.master_data.reference.service import VehicleTypeService
    from app.modules.master_data.vehicle.service import VehicleService
    from app.modules.master_data.driver.service import DriverService
    from app.modules.document_config.service import (
        DocumentTypeService, NumberingSchemeService)

    user = _login(client, db, codes=[
        "tripticket.view", "tripticket.create", "tripticket.update"])
    branch = BranchService().create(code="BR-SS", name="Smart Select Branch")
    vt = VehicleTypeService().create(code="LV-SS", name="Light", category="LIGHT")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Innova", year=2024,
        branch_id=branch.id, conduction_number="SS-000")
    driver = DriverService().create(
        employee_number="EMP-SS1", first_name="Ana", last_name="Reyes",
        license_number="LIC-SS1", license_expiry=date(2030, 1, 1),
        license_type="PROFESSIONAL", branch_id=branch.id)
    DocumentTypeService().create(code="TT", name="Trip Ticket",
                                 requires_approval=False, auto_numbering=True)

    resp = client.post("/transactions/trip-tickets/new", data={
        "vehicle_id": str(vehicle.id), "driver_id": str(driver.id),
        "destination": "Tagaytay", "purpose": "Site visit",
        "departure_datetime": "2026-07-20T08:00",
        "odometer_out": "5000",
    }, follow_redirects=True)
    assert resp.status_code == 200

    from app.modules.transactions.trip_ticket.models import TripTicket
    trip = TripTicket.query.first()
    assert trip is not None
    assert trip.vehicle_id == vehicle.id
    assert trip.driver_id == driver.id
