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
from app.modules.transactions.atd.service import ATDService
from app.modules.transactions.trip_ticket.service import TripTicketService
from app.modules.transactions.vehicle_movement.service import VehicleMovementService
from app.modules.transactions.tire_txn.service import TireTransactionService
from app.modules.transactions.battery_txn.service import BatteryTransactionService


def _login(client, db, *, codes=()):
    role = Role(name="TokenAllRole")
    for code in codes:
        m, a = code.split(".")
        p = Permission.query.filter_by(code=code).first()
        if p is None:
            p = Permission(code=code, module=m, action=a)
            db.session.add(p)
        role.permissions.append(p)
    u = User(username="token_all_user", email="token_all_user@x.com",
             password_hash=hash_password("pw123456"))
    u.roles.append(role)
    db.session.add_all([role, u])
    db.session.commit()
    client.post("/login", data={"username": "token_all_user", "password": "pw123456"})
    return u


@pytest.fixture()
def env(db):
    branch = BranchService().create(code="BR-TOKENALL", name="Token All Branch")
    vt = VehicleTypeService().create(code="LV-TOKENALL", name="Light", category="LIGHT")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Ford", model="Ranger", year=2024,
        branch_id=branch.id, conduction_number="TOKENALL-000", plate_number="TKN-001")
    driver = DriverService().create(
        employee_number="EMP-TOKENALL1", first_name="Test", last_name="Driver",
        license_number="LIC-TOKENALL1", license_expiry=date(2030, 1, 1),
        license_type="PROFESSIONAL", branch_id=branch.id)
    return branch, vt, vehicle, driver


def test_atd_purpose_resolves_tokens(client, db, env):
    branch, vt, vehicle, driver = env
    _login(client, db, codes=["atd.view", "atd.print"])
    atd = ATDService().create(
        vehicle_id=vehicle.id, driver_id=driver.id,
        purpose="Pull-out for pm2 pm3, plate pm4.",
        valid_from=date.today(), valid_to=date.today(), user=None)
    resp = client.get(f"/transactions/atd/{atd.id}/print")
    assert b"Pull-out for Ford Ranger, plate TKN-001." in resp.data


def test_tripticket_purpose_resolves_tokens(client, db, env):
    branch, vt, vehicle, driver = env
    _login(client, db, codes=["tripticket.view", "tripticket.print"])
    trip = TripTicketService().create(
        vehicle_id=vehicle.id, driver_id=driver.id, destination="Baguio",
        purpose="Trip using pm2 pm3", departure_datetime=datetime(2026, 7, 20, 8, 0),
        odometer_out=1000, user=None)
    resp = client.get(f"/transactions/trip-tickets/{trip.id}/print")
    assert b"Trip using Ford Ranger" in resp.data


def test_vehiclemovement_purpose_and_remarks_resolve_tokens(client, db, env):
    branch, vt, vehicle, driver = env
    _login(client, db, codes=["vehiclemovement.view", "vehiclemovement.print"])
    mv = VehicleMovementService().create(
        vehicle_id=vehicle.id, movement_type="TRANSFER",
        from_location="Manila", to_location="Cebu", movement_date=date.today(),
        purpose="Moving pm4", user=None)
    resp = client.get(f"/transactions/vehicle-movements/{mv.id}/print")
    assert b"Moving TKN-001" in resp.data


def test_tiretxn_remarks_resolve_tokens(client, db, env):
    branch, vt, vehicle, driver = env
    _login(client, db, codes=["tiretxn.view", "tiretxn.print"])
    tire = TireService().create(
        serial_number="TIRE-TOKENALL-1", brand="Bridgestone", size="195/65R15",
        tire_type="RADIAL", status="IN_STOCK", branch_id=branch.id)
    txn = TireTransactionService().create(
        tire_id=tire.id, vehicle_id=vehicle.id, action="MOUNT",
        transaction_date=date.today(), remarks="Mounted on pm4", user=None)
    resp = client.get(f"/transactions/tire-transactions/{txn.id}/print")
    assert b"Mounted on TKN-001" in resp.data


def test_batterytxn_remarks_resolve_tokens(client, db, env):
    branch, vt, vehicle, driver = env
    _login(client, db, codes=["batterytxn.view", "batterytxn.print"])
    battery = BatteryService().create(
        serial_number="BATT-TOKENALL-1", brand="Motolite", capacity_ah=65,
        status="IN_STOCK", branch_id=branch.id)
    txn = BatteryTransactionService().create(
        battery_id=battery.id, vehicle_id=vehicle.id, action="MOUNT",
        transaction_date=date.today(), remarks="Installed on pm4", user=None)
    resp = client.get(f"/transactions/battery-transactions/{txn.id}/print")
    assert b"Installed on TKN-001" in resp.data
