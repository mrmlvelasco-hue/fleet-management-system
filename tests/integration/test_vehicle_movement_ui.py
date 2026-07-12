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
    role = Role(name="VMFormRole")
    for code in codes:
        m, a = code.split(".")
        p = Permission(code=code, module=m, action=a)
        db.session.add(p)
        role.permissions.append(p)
    u = User(username="reza", email="reza@x.com",
             password_hash=hash_password("pw123456"))
    u.roles.append(role)
    db.session.add_all([role, u])
    db.session.commit()
    client.post("/login", data={"username": "reza", "password": "pw123456"})
    return u


def test_vehiclemovement_form_shows_new_fields(client, db):
    _login(client, db, codes=["vehiclemovement.view", "vehiclemovement.create"])
    resp = client.get("/transactions/vehicle-movements/new")
    assert resp.status_code == 200
    assert b'id="vmDriverSelect"' in resp.data
    assert b'name="employee_responsible"' in resp.data
    assert b'name="purpose"' in resp.data
    assert b'name="movement_start_datetime"' in resp.data


def test_create_vehicle_movement_defaults_driver_and_shows_on_detail(client, db):
    user = _login(client, db, codes=[
        "vehiclemovement.view", "vehiclemovement.create"])
    branch = BranchService().create(code="BR-VMUI", name="VM UI Branch")
    vt = VehicleTypeService().create(code="LV-VMUI", name="Light", category="LIGHT")
    driver = DriverService().create(
        employee_number="EMP-VMUI1", first_name="Nora", last_name="Lopez",
        license_number="LIC-VMUI1", license_expiry=date(2030, 1, 1),
        license_type="PROFESSIONAL", branch_id=branch.id)
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Isuzu", model="Elf", year=2024,
        branch_id=branch.id, conduction_number="VMUI-000",
        assigned_driver_id=driver.id)
    DocumentTypeService().create(code="VM", name="Vehicle Movement",
                                 requires_approval=False, auto_numbering=True)

    resp = client.post("/transactions/vehicle-movements/new", data={
        "vehicle_id": str(vehicle.id), "movement_type": "TRANSFER",
        "from_location": "HQ", "to_location": "Branch B",
        "movement_date": "2026-07-15",
        "purpose": "Relocate to Branch B",
    }, follow_redirects=True)
    assert resp.status_code == 200

    from app.modules.transactions.vehicle_movement.models import VehicleMovement
    mv = VehicleMovement.query.filter_by(vehicle_id=vehicle.id).first()
    assert mv.driver_id == driver.id
    assert mv.purpose == "Relocate to Branch B"

    detail_resp = client.get(f"/transactions/vehicle-movements/{mv.id}")
    assert detail_resp.status_code == 200
    assert b"Lopez" in detail_resp.data
    assert b"Relocate to Branch B" in detail_resp.data
