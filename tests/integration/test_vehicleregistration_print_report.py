from datetime import date

import pytest

from app.core.security.password import hash_password
from app.modules.user_management.models import User, Role, Permission
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.driver.service import DriverService
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.org.service import BranchService
from app.modules.registration_config.service import RegistrationTemplateService
from app.modules.transactions.vehicle_registration.service import (
    VehicleRegistrationService)


def _login(client, db, *, codes=()):
    role = Role(name="RegPrintRole")
    for code in codes:
        m, a = code.split(".")
        p = Permission.query.filter_by(code=code).first()
        if p is None:
            p = Permission(code=code, module=m, action=a)
            db.session.add(p)
        role.permissions.append(p)
    u = User(username="reg_print_user", email="reg_print_user@x.com",
             password_hash=hash_password("pw123456"))
    u.roles.append(role)
    db.session.add_all([role, u])
    db.session.commit()
    client.post("/login", data={"username": "reg_print_user", "password": "pw123456"})
    return u


@pytest.fixture()
def env(db):
    branch = BranchService().create(code="BR-REGPRINT", name="Reg Print Branch")
    vt = VehicleTypeService().create(code="LV-REGPRINT", name="Light", category="LIGHT")
    driver = DriverService().create(
        employee_number="EMP-REGPRINT1", first_name="Liza", last_name="Cruz",
        license_number="LIC-REGPRINT1", license_expiry=date(2030, 1, 1),
        license_type="PROFESSIONAL", branch_id=branch.id,
        job_title="Area Sales Manager")
    return branch, vt, driver


def test_print_works_for_any_vehicle_brand(client, db, env):
    """Confirms the report is fully dynamic -- not hardcoded to one
    brand, works identically across the whole fleet."""
    branch, vt, driver = env
    _login(client, db, codes=["vehicleregistration.view", "vehicleregistration.print"])

    for brand, model in [("Ford", "Everest"), ("Mitsubishi", "Montero"),
                         ("Nissan", "Navara")]:
        vehicle = VehicleService().create(
            vehicle_type_id=vt.id, brand=brand, model=model, year=2024,
            branch_id=branch.id, conduction_number=f"REGPRINT-{brand}",
            assigned_driver_id=driver.id)
        RegistrationTemplateService().create(
            vehicle_type_id=vt.id, interval_years=3,
            items=[{"activity_code": "OR-CR", "activity_description": "Renew OR/CR",
                   "sort_order": 1}])
        reg = VehicleRegistrationService().create(
            vehicle_id=vehicle.id, registration_type="NEW",
            registration_date=date.today(), user=None)

        resp = client.get(f"/transactions/vehicle-registrations/{reg.id}/print")
        assert resp.status_code == 200
        assert brand.encode() in resp.data
        assert model.encode() in resp.data
        assert b"Renew OR/CR" in resp.data
        assert b"Liza Cruz" in resp.data
        assert b"Area Sales Manager" in resp.data


def test_print_includes_qr_code(client, db, env):
    branch, vt, driver = env
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2024,
        branch_id=branch.id, conduction_number="REGPRINT-QR")
    reg = VehicleRegistrationService().create(
        vehicle_id=vehicle.id, registration_type="NEW",
        registration_date=date.today(), user=None)
    _login(client, db, codes=["vehicleregistration.view", "vehicleregistration.print"])
    resp = client.get(f"/transactions/vehicle-registrations/{reg.id}/print")
    assert resp.status_code == 200
    assert b"qrCanvas" in resp.data


def test_print_shows_requester_signature(client, db, env):
    branch, vt, driver = env
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2024,
        branch_id=branch.id, conduction_number="REGPRINT-SIG")
    requester = _login(client, db, codes=["vehicleregistration.view",
                                          "vehicleregistration.print"])
    reg = VehicleRegistrationService().create(
        vehicle_id=vehicle.id, registration_type="NEW",
        registration_date=date.today(), user=requester)
    resp = client.get(f"/transactions/vehicle-registrations/{reg.id}/print")
    assert resp.status_code == 200
    assert requester.full_name.encode() in resp.data
