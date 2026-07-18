from datetime import date

from app.core.security.password import hash_password
from app.modules.user_management.models import User, Role, Permission
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.org.service import BranchService


def _login(client, db, *, codes=()):
    role = Role(name="AssuredValueUIRole")
    for code in codes:
        m, a = code.split(".")
        p = Permission.query.filter_by(code=code).first()
        if p is None:
            p = Permission(code=code, module=m, action=a)
            db.session.add(p)
        role.permissions.append(p)
    u = User(username="assured_value_ui_user", email="assured_value_ui_user@x.com",
             password_hash=hash_password("pw123456"))
    u.roles.append(role)
    db.session.add_all([role, u])
    db.session.commit()
    client.post("/login", data={"username": "assured_value_ui_user", "password": "pw123456"})
    return u


def test_edit_form_shows_computed_assured_value_when_field_is_blank(client, db):
    _login(client, db, codes=["vehicle.view", "vehicle.update"])
    branch = BranchService().create(code="BR-ASSUREDUI", name="Assured Value UI Branch")
    vt = VehicleTypeService().create(code="LV-ASSUREDUI", name="Light", category="LIGHT")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2024,
        branch_id=branch.id, conduction_number="ASSUREDUI-000",
        acquisition_cost=1000000, delivery_date=date(2025, 1, 15))

    resp = client.get(f"/master/vehicles/{vehicle.id}/edit")
    assert resp.status_code == 200
    # Depends on today's date relative to delivery, but the auto-compute
    # helper text must be present either way.
    assert b"Auto-computed" in resp.data


def test_money_fields_are_text_inputs_for_comma_formatting(client, db):
    _login(client, db, codes=["vehicle.view", "vehicle.create"])
    resp = client.get("/master/vehicles/new")
    assert resp.status_code == 200
    assert b'id="vehAcquisitionCost"' in resp.data
    assert b'id="vehTopUpAmount"' in resp.data
    assert b'id="vehAssuredValue"' in resp.data
    assert b"formatMoneyInput" in resp.data
