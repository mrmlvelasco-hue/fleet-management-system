from datetime import date, timedelta

import pytest

from app.core.security.password import hash_password
from app.modules.user_management.models import User, Role, Permission
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.org.service import BranchService
from app.modules.registration_config.service import RegistrationTemplateService
from app.modules.transactions.vehicle_registration.service import (
    VehicleRegistrationService)


def _login(client, db, *, codes=()):
    role = Role(name="RegDashboardRole")
    for code in codes:
        m, a = code.split(".")
        p = Permission.query.filter_by(code=code).first()
        if p is None:
            p = Permission(code=code, module=m, action=a)
            db.session.add(p)
        role.permissions.append(p)
    u = User(username="regdashboard_user", email="regdashboard_user@x.com",
             password_hash=hash_password("pw123456"))
    u.roles.append(role)
    db.session.add_all([role, u])
    db.session.commit()
    client.post("/login", data={"username": "regdashboard_user", "password": "pw123456"})
    return u


def test_dashboard_shows_due_registration_widget(client, db):
    branch = BranchService().create(code="BR-REGDASH", name="Reg Dash Branch")
    vt = VehicleTypeService().create(code="LV-REGDASH", name="Light", category="LIGHT")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2020,
        branch_id=branch.id, conduction_number="REGDASH-000")
    RegistrationTemplateService().create(vehicle_type_id=vt.id, interval_years=3,
                                         notify_before_days=30)
    reg = VehicleRegistrationService().create(
        vehicle_id=vehicle.id, registration_type="NEW",
        registration_date=date.today() - timedelta(days=3 * 365 + 10), user=None)
    reg.status = "COMPLETED"
    db.session.commit()

    _login(client, db, codes=["vehicle.view", "vehicleregistration.create"])
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"Vehicles Due for Registration Renewal" in resp.data
    assert b"REGDASH-000" in resp.data
    assert b"OVERDUE" in resp.data


def test_dashboard_registration_link_prefills_new_form(client, db):
    branch = BranchService().create(code="BR-REGDASH2", name="Reg Dash Branch 2")
    vt = VehicleTypeService().create(code="LV-REGDASH2", name="Light", category="LIGHT")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2020,
        branch_id=branch.id, conduction_number="REGDASH-001")
    RegistrationTemplateService().create(vehicle_type_id=vt.id, interval_years=3,
                                         notify_before_days=30)
    reg = VehicleRegistrationService().create(
        vehicle_id=vehicle.id, registration_type="NEW",
        registration_date=date.today() - timedelta(days=3 * 365 + 10), user=None)
    reg.status = "COMPLETED"
    db.session.commit()

    _login(client, db, codes=["vehicle.view", "vehicleregistration.create"])
    resp = client.get("/")
    assert f"/transactions/vehicle-registrations/new?vehicle_id={vehicle.id}".encode() in resp.data

    prefilled = client.get(
        f"/transactions/vehicle-registrations/new?vehicle_id={vehicle.id}"
        f"&registration_type=RENEWAL&registration_date={date.today().isoformat()}")
    assert prefilled.status_code == 200
    assert f'value="{vehicle.id}" selected'.encode() in prefilled.data
    assert b'value="RENEWAL" selected' in prefilled.data


def test_new_registration_form_defaults_to_new_type_without_prefill(client, db):
    """Confirms the earlier default-value fix: visiting the plain New
    Registration page (no query params at all) still defaults to NEW,
    not RENEWAL, for the normal first-time registration flow."""
    _login(client, db, codes=["vehicleregistration.create"])
    resp = client.get("/transactions/vehicle-registrations/new")
    assert resp.status_code == 200
    assert b'value="RENEWAL" selected' not in resp.data
