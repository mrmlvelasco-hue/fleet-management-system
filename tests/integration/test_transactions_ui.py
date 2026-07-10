from datetime import date

from app.core.security.password import hash_password
from app.modules.user_management.models import User, Role, Permission
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.driver.service import DriverService
from app.modules.document_config.service import (
    DocumentTypeService, NumberingSchemeService)


def _login(client, db, *, codes=()):
    role = Role(name="TxRole")
    for code in codes:
        m, a = code.split(".")
        p = Permission(code=code, module=m, action=a)
        db.session.add(p)
        role.permissions.append(p)
    u = User(username="omar", email="omar@x.com",
             password_hash=hash_password("pw123456"))
    u.roles.append(role)
    db.session.add_all([role, u])
    db.session.commit()
    client.post("/login", data={"username": "omar", "password": "pw123456"})
    return u


def _seed_vehicle_and_driver(db):
    branch = BranchService().create(code="BR-INT", name="Integration Branch")
    vt = VehicleTypeService().create(code="LV-INT", name="Light", category="LIGHT")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Wigo", year=2024,
        branch_id=branch.id, conduction_number="INT-000")
    driver = DriverService().create(
        employee_number="EMP-INT1", first_name="Ana", last_name="Reyes",
        license_number="LIC-INT1", license_expiry=date(2030, 1, 1),
        license_type="PROFESSIONAL", branch_id=branch.id)
    DocumentTypeService().create(code="TT", name="Trip Ticket",
                                 requires_approval=False, auto_numbering=True)
    return vehicle, driver


def test_tripticket_list_403_without_permission(client, db):
    _login(client, db)
    assert client.get("/transactions/trip-tickets").status_code == 403


def test_tripticket_list_200_with_permission(client, db):
    _login(client, db, codes=["tripticket.view"])
    assert client.get("/transactions/trip-tickets").status_code == 200


def test_tripticket_new_form_renders(client, db):
    _login(client, db, codes=["tripticket.view", "tripticket.create"])
    _seed_vehicle_and_driver(db)
    resp = client.get("/transactions/trip-tickets/new")
    assert resp.status_code == 200
    assert b"Destination" in resp.data


def test_atd_list_renders(client, db):
    _login(client, db, codes=["atd.view"])
    assert client.get("/transactions/atd").status_code == 200


def test_atd_new_form_renders(client, db):
    _login(client, db, codes=["atd.view", "atd.create"])
    _seed_vehicle_and_driver(db)
    resp = client.get("/transactions/atd/new")
    assert resp.status_code == 200


def test_vehiclemovement_list_renders(client, db):
    _login(client, db, codes=["vehiclemovement.view"])
    assert client.get("/transactions/vehicle-movements").status_code == 200


def test_vehiclemovement_new_form_renders(client, db):
    _login(client, db, codes=["vehiclemovement.view", "vehiclemovement.create"])
    _seed_vehicle_and_driver(db)
    resp = client.get("/transactions/vehicle-movements/new")
    assert resp.status_code == 200


def test_tripticket_full_flow_create_detail_print(client, db):
    user = _login(client, db, codes=[
        "tripticket.view", "tripticket.create", "tripticket.update",
        "tripticket.print"])
    vehicle, driver = _seed_vehicle_and_driver(db)

    from app.modules.document_config.service import NumberingSchemeService
    from app.modules.document_config.models import DocumentType
    dt = DocumentType.query.filter_by(code="TT").first()
    NumberingSchemeService().create(document_type_id=dt.id, prefix="TT",
                                    include_year=True, digit_count=6,
                                    reset_policy="YEARLY")

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

    detail_resp = client.get(f"/transactions/trip-tickets/{trip.id}")
    assert detail_resp.status_code == 200

    submit_resp = client.post(
        f"/transactions/trip-tickets/{trip.id}/submit", follow_redirects=True)
    assert submit_resp.status_code == 200

    print_resp = client.get(f"/transactions/trip-tickets/{trip.id}/print")
    assert print_resp.status_code == 200
    assert b"TRIP TICKET" in print_resp.data
