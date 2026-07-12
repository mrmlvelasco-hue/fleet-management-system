from app.core.security.password import hash_password
from app.modules.user_management.models import User, Role, Permission
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.vehicle_brand.service import (
    VehicleBrandService, VehicleModelService)


def _login(client, db, *, codes=()):
    role = Role(name="FormEchoRole")
    for code in codes:
        m, a = code.split(".")
        p = Permission(code=code, module=m, action=a)
        db.session.add(p)
        role.permissions.append(p)
    u = User(username="widad", email="widad@x.com",
             password_hash=hash_password("pw123456"))
    u.roles.append(role)
    db.session.add_all([role, u])
    db.session.commit()
    client.post("/login", data={"username": "widad", "password": "pw123456"})
    return u


def test_invalid_acquisition_date_preserves_all_other_form_data(client, db):
    """Reproduces the reported bug exactly: submit a full Vehicle form with
    an invalid Acquisition Date ('01/05/2026') — the form must redisplay
    with every other field still filled in, not blank, and the invalid
    date field highlighted."""
    _login(client, db, codes=["vehicle.view", "vehicle.create"])
    branch = BranchService().create(code="BR-ECHO", name="Echo Branch")
    vt = VehicleTypeService().create(code="LV-ECHO", name="Light", category="LIGHT")
    toyota = VehicleBrandService().create(name="Toyota")
    VehicleModelService().create(brand_id=toyota.id, name="Hilux")

    resp = client.post("/master/vehicles/new", data={
        "vehicle_type_id": str(vt.id), "brand": "Toyota", "model": "Hilux",
        "year": "2024", "color": "White", "branch_id": str(branch.id),
        "conduction_number": "ECHO-001", "chassis_number": "CHS-999",
        "engine_number": "ENG-888", "acquisition_date": "01/05/2026",
        "acquisition_cost": "1500000", "notes": "Test note for echo",
    })
    assert resp.status_code == 200
    html = resp.data.decode()

    # The friendly error is shown
    assert "Invalid date format" in html or "invalid" in html.lower()

    # Every other field the user typed must still be present, not blank
    assert 'value="ECHO-001"' in html
    assert 'value="CHS-999"' in html
    assert 'value="ENG-888"' in html
    assert 'value="2024"' in html
    assert 'value="White"' in html
    assert 'value="1500000"' in html
    assert "Test note for echo" in html
    assert 'value="Toyota" selected' in html
    assert 'value="Hilux" selected' in html
    assert f'value="{branch.id}" selected' in html

    # The offending field itself is highlighted
    assert "is-invalid" in html

    # And nothing was actually created
    from app.modules.master_data.vehicle.models import Vehicle
    assert Vehicle.query.filter_by(conduction_number="ECHO-001").count() == 0


def test_valid_resubmission_after_fixing_the_date_succeeds(client, db):
    _login(client, db, codes=["vehicle.view", "vehicle.create"])
    branch = BranchService().create(code="BR-ECHO2", name="Echo Branch 2")
    vt = VehicleTypeService().create(code="LV-ECHO2", name="Light", category="LIGHT")
    toyota = VehicleBrandService().create(name="Toyota2")
    VehicleModelService().create(brand_id=toyota.id, name="Vios2")

    resp = client.post("/master/vehicles/new", data={
        "vehicle_type_id": str(vt.id), "brand": "Toyota2", "model": "Vios2",
        "year": "2024", "branch_id": str(branch.id),
        "conduction_number": "ECHO-002", "acquisition_date": "2026-05-01",
    }, follow_redirects=True)
    assert resp.status_code == 200
    from app.modules.master_data.vehicle.models import Vehicle
    vehicle = Vehicle.query.filter_by(conduction_number="ECHO-002").first()
    assert vehicle is not None
    assert str(vehicle.acquisition_date) == "2026-05-01"


def test_edit_form_also_preserves_submitted_data_on_invalid_date(client, db):
    """Same bug, edit path: previously showed the stale saved values
    instead of what the user just typed when a validation error occurred."""
    _login(client, db, codes=["vehicle.view", "vehicle.create", "vehicle.update"])
    branch = BranchService().create(code="BR-ECHO3", name="Echo Branch 3")
    vt = VehicleTypeService().create(code="LV-ECHO3", name="Light", category="LIGHT")
    toyota = VehicleBrandService().create(name="Toyota3")
    VehicleModelService().create(brand_id=toyota.id, name="Hilux3")

    create_resp = client.post("/master/vehicles/new", data={
        "vehicle_type_id": str(vt.id), "brand": "Toyota3", "model": "Hilux3",
        "year": "2024", "branch_id": str(branch.id),
        "conduction_number": "ECHO-003",
    }, follow_redirects=True)
    from app.modules.master_data.vehicle.models import Vehicle
    vehicle = Vehicle.query.filter_by(conduction_number="ECHO-003").first()

    edit_resp = client.post(f"/master/vehicles/{vehicle.id}/edit", data={
        "vehicle_type_id": str(vt.id), "brand": "Toyota3", "model": "Hilux3",
        "year": "2025", "color": "Red", "branch_id": str(branch.id),
        "acquisition_date": "01/05/2026",  # invalid
    })
    assert edit_resp.status_code == 200
    html = edit_resp.data.decode()
    assert 'value="2025"' in html  # the NEW year just typed, not the old one
    assert 'value="Red"' in html
    assert "is-invalid" in html
