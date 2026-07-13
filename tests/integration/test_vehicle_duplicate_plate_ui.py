from app.core.security.password import hash_password
from app.modules.user_management.models import User, Role, Permission
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.vehicle.service import VehicleService


def _login(client, db, *, codes=()):
    role = Role(name="VehDupUIRole")
    for code in codes:
        m, a = code.split(".")
        p = Permission.query.filter_by(code=code).first()
        if p is None:
            p = Permission(code=code, module=m, action=a)
            db.session.add(p)
        role.permissions.append(p)
    u = User(username="vehdup_ui", email="vehdup_ui@x.com",
             password_hash=hash_password("pw123456"))
    u.roles.append(role)
    db.session.add_all([role, u])
    db.session.commit()
    client.post("/login", data={"username": "vehdup_ui", "password": "pw123456"})
    return u


def test_duplicate_plate_number_shows_friendly_error_and_preserves_form_data(client, db):
    _login(client, db, codes=["vehicle.view", "vehicle.create"])
    branch = BranchService().create(code="BR-VEHDUP", name="Veh Dup Branch")
    vt = VehicleTypeService().create(code="LV-VEHDUP", name="Light", category="LIGHT")
    VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2024,
        branch_id=branch.id, conduction_number="VEHDUP-000",
        plate_number="VEHDUP-PLATE")

    resp = client.post("/master/vehicles/new", data={
        "vehicle_type_id": str(vt.id), "brand": "Honda", "model": "City",
        "year": "2024", "color": "Blue", "branch_id": str(branch.id),
        "conduction_number": "VEHDUP-001", "plate_number": "VEHDUP-PLATE",
    })
    assert resp.status_code == 200
    html = resp.data.decode()

    # Friendly message, never a raw DB/technical error
    assert "already exists" in html.lower()
    assert "IntegrityError" not in html
    assert "sqlalchemy" not in html.lower()
    assert "traceback" not in html.lower()

    # Form data preserved — user doesn't have to retype everything
    assert 'value="VEHDUP-001"' in html
    assert 'value="Blue"' in html

    # The offending field is highlighted
    assert "is-invalid" in html

    # And no duplicate was actually created
    from app.modules.master_data.vehicle.models import Vehicle
    assert Vehicle.query.filter_by(conduction_number="VEHDUP-001").count() == 0
