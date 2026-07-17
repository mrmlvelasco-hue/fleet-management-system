from datetime import date, datetime

import pytest

from app.core.security.password import hash_password
from app.modules.user_management.models import User, Role, Permission
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.driver.service import DriverService
from app.modules.master_data.tire.service import TireService
from app.modules.master_data.battery.service import BatteryService
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.org.service import BranchService
from app.modules.transactions.trip_ticket.service import TripTicketService
from app.modules.transactions.vehicle_movement.service import VehicleMovementService
from app.modules.transactions.purchase_request.service import PurchaseRequestService
from app.modules.transactions.tire_txn.service import TireTransactionService
from app.modules.transactions.battery_txn.service import BatteryTransactionService


def _login(client, db, *, codes=()):
    role = Role(name="AllPrintRole")
    for code in codes:
        m, a = code.split(".")
        p = Permission.query.filter_by(code=code).first()
        if p is None:
            p = Permission(code=code, module=m, action=a)
            db.session.add(p)
        role.permissions.append(p)
    u = User(username="all_print_user", email="all_print_user@x.com",
             password_hash=hash_password("pw123456"))
    u.roles.append(role)
    db.session.add_all([role, u])
    db.session.commit()
    client.post("/login", data={"username": "all_print_user", "password": "pw123456"})
    return u


@pytest.fixture()
def env(db):
    branch = BranchService().create(code="BR-ALLPRINT", name="All Print Branch")
    vt = VehicleTypeService().create(code="LV-ALLPRINT", name="Light", category="LIGHT")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2024,
        branch_id=branch.id, conduction_number="ALLPRINT-000")
    driver = DriverService().create(
        employee_number="EMP-ALLPRINT1", first_name="Test", last_name="Driver",
        license_number="LIC-ALLPRINT1", license_expiry=date(2030, 1, 1),
        license_type="PROFESSIONAL", branch_id=branch.id)
    return branch, vt, vehicle, driver


def test_tripticket_print_has_qr_and_signatures(client, db, env):
    branch, vt, vehicle, driver = env
    requester = _login(client, db, codes=["tripticket.view", "tripticket.print"])
    trip = TripTicketService().create(
        vehicle_id=vehicle.id, driver_id=driver.id, destination="Baguio",
        purpose="Delivery", departure_datetime=datetime(2026, 7, 20, 8, 0),
        odometer_out=1000, user=requester)
    resp = client.get(f"/transactions/trip-tickets/{trip.id}/print")
    assert resp.status_code == 200
    assert b"qrCanvas" in resp.data
    assert requester.full_name.encode() in resp.data


def test_vehiclemovement_print_shows_new_fields(client, db, env):
    branch, vt, vehicle, driver = env
    _login(client, db, codes=["vehiclemovement.view", "vehiclemovement.print"])
    mv = VehicleMovementService().create(
        vehicle_id=vehicle.id, movement_type="TRANSFER",
        from_location="Manila", to_location="Cebu",
        movement_date=date.today(), driver_id=driver.id,
        employee_responsible="Test Employee", purpose="Branch transfer",
        user=None)
    resp = client.get(f"/transactions/vehicle-movements/{mv.id}/print")
    assert resp.status_code == 200
    assert b"qrCanvas" in resp.data
    assert b"Test Driver" in resp.data
    assert b"Test Employee" in resp.data
    assert b"Branch transfer" in resp.data


def test_purchaserequest_print_has_qr(client, db, env):
    branch, vt, vehicle, driver = env
    requester = _login(client, db, codes=["purchaserequest.view",
                                          "purchaserequest.print"])
    pr = PurchaseRequestService().create(
        description="Test PR", needed_by_date=date.today(),
        lines=[{"item_description": "Oil Filter", "quantity": 2, "unit_cost": 500}],
        user=requester)
    resp = client.get(f"/transactions/purchase-requests/{pr.id}/print")
    assert resp.status_code == 200
    assert b"qrCanvas" in resp.data
    assert requester.full_name.encode() in resp.data


def test_tiretxn_print_has_qr_and_signatures(client, db, env):
    branch, vt, vehicle, driver = env
    requester = _login(client, db, codes=["tiretxn.view", "tiretxn.print"])
    tire = TireService().create(
        serial_number="TIRE-ALLPRINT-1", brand="Bridgestone", size="195/65R15",
        tire_type="RADIAL", status="IN_STOCK", branch_id=branch.id)
    txn = TireTransactionService().create(
        tire_id=tire.id, vehicle_id=vehicle.id, action="MOUNT",
        transaction_date=date.today(), user=requester)
    resp = client.get(f"/transactions/tire-transactions/{txn.id}/print")
    assert resp.status_code == 200
    assert b"qrCanvas" in resp.data
    assert requester.full_name.encode() in resp.data


def test_batterytxn_print_has_qr_and_signatures(client, db, env):
    branch, vt, vehicle, driver = env
    requester = _login(client, db, codes=["batterytxn.view", "batterytxn.print"])
    battery = BatteryService().create(
        serial_number="BATT-ALLPRINT-1", brand="Motolite", capacity_ah=65,
        status="IN_STOCK", branch_id=branch.id)
    txn = BatteryTransactionService().create(
        battery_id=battery.id, vehicle_id=vehicle.id, action="MOUNT",
        transaction_date=date.today(), user=requester)
    resp = client.get(f"/transactions/battery-transactions/{txn.id}/print")
    assert resp.status_code == 200
    assert b"qrCanvas" in resp.data
    assert requester.full_name.encode() in resp.data
