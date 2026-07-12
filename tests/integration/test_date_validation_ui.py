from datetime import date

from app.core.security.password import hash_password
from app.modules.user_management.models import User, Role, Permission
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.document_config.service import (
    DocumentTypeService, NumberingSchemeService)


def _login(client, db, *, codes=()):
    role = Role(name="DateFixRole")
    for code in codes:
        m, a = code.split(".")
        p = Permission(code=code, module=m, action=a)
        db.session.add(p)
        role.permissions.append(p)
    u = User(username="tomas", email="tomas@x.com",
             password_hash=hash_password("pw123456"))
    u.roles.append(role)
    db.session.add_all([role, u])
    db.session.commit()
    client.post("/login", data={"username": "tomas", "password": "pw123456"})
    return u


def test_vehicle_registration_with_slash_date_shows_friendly_error_not_crash(client, db):
    """Regression: the reported crash — ValueError: Invalid isoformat
    string: '01/05/2026' — must now show a friendly flash message and
    re-render the form (HTTP 200), not a 500 error."""
    user = _login(client, db, codes=[
        "vehicleregistration.view", "vehicleregistration.create"])
    branch = BranchService().create(code="BR-DATE", name="Date Branch")
    vt = VehicleTypeService().create(code="LV-DATE", name="Light", category="LIGHT")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2024,
        branch_id=branch.id, conduction_number="DATE-000")
    DocumentTypeService().create(code="VR", name="Vehicle Registration",
                                 requires_approval=False, auto_numbering=True)

    resp = client.post("/transactions/vehicle-registrations/new", data={
        "vehicle_id": str(vehicle.id), "registration_type": "NEW",
        "registration_date": "01/05/2026",  # non-ISO, the exact reported bug
    }, follow_redirects=True)

    assert resp.status_code == 200  # not a 500 crash
    assert b"Invalid date format" in resp.data
    assert b"YYYY-MM-DD" in resp.data

    from app.modules.transactions.vehicle_registration.models import (
        VehicleRegistration)
    assert VehicleRegistration.query.count() == 0  # nothing was created


def test_vehicle_registration_with_valid_iso_date_succeeds(client, db):
    user = _login(client, db, codes=[
        "vehicleregistration.view", "vehicleregistration.create"])
    branch = BranchService().create(code="BR-DATE2", name="Date Branch 2")
    vt = VehicleTypeService().create(code="LV-DATE2", name="Light", category="LIGHT")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2024,
        branch_id=branch.id, conduction_number="DATE-001")
    dt = DocumentTypeService().create(code="VR2", name="Vehicle Registration 2",
                                      requires_approval=False, auto_numbering=True)
    NumberingSchemeService().create(document_type_id=dt.id, prefix="VR2",
                                    include_year=True, digit_count=6,
                                    reset_policy="YEARLY")

    resp = client.post("/transactions/vehicle-registrations/new", data={
        "vehicle_id": str(vehicle.id), "registration_type": "NEW",
        "registration_date": "2026-01-05",
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert b"Invalid date format" not in resp.data


def test_driver_with_missing_required_license_expiry_shows_friendly_error(client, db):
    user = _login(client, db, codes=["driver.view", "driver.create"])
    branch = BranchService().create(code="BR-DATE3", name="Date Branch 3")
    resp = client.post("/master/drivers/new", data={
        "employee_number": "EMP-DATE1", "first_name": "Test",
        "last_name": "Driver", "license_number": "LIC-DATE1",
        "license_expiry": "",  # missing required field
        "license_type": "PROFESSIONAL", "branch_id": str(branch.id),
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert b"required" in resp.data.lower()

    from app.modules.master_data.driver.models import Driver
    assert Driver.query.count() == 0


def test_vehicle_acquisition_date_bad_format_shows_friendly_error(client, db):
    """Regression: Vehicle master's Acquisition Date field uses a flatpickr
    text input, which is the most likely real-world source of non-ISO
    submissions if the JS widget fails to load."""
    user = _login(client, db, codes=["vehicle.view", "vehicle.create"])
    branch = BranchService().create(code="BR-DATE4", name="Date Branch 4")
    vt = VehicleTypeService().create(code="LV-DATE4", name="Light", category="LIGHT")
    resp = client.post("/master/vehicles/new", data={
        "vehicle_type_id": str(vt.id), "brand": "Toyota", "model": "Wigo",
        "year": "2024", "branch_id": str(branch.id),
        "conduction_number": "DATE-004",
        "acquisition_date": "05-Jan-2026",  # bad format
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert b"Invalid date format" in resp.data
