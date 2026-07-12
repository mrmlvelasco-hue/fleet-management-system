from app.core.security.password import hash_password
from app.modules.user_management.models import User, Role, Permission
from app.modules.user_management.org_scope_service import UserOrgScopeService
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.vehicle.service import VehicleService


def _make_scoped_user(client, db, username, branch, codes):
    role = Role(name=f"VehScopeUIRole-{username}")
    for code in codes:
        m, a = code.split(".")
        p = Permission.query.filter_by(code=code).first()
        if p is None:
            p = Permission(code=code, module=m, action=a)
            db.session.add(p)
        role.permissions.append(p)
    u = User(username=username, email=f"{username}@x.com",
             password_hash=hash_password("pw123456"))
    u.roles.append(role)
    db.session.add_all([role, u])
    db.session.commit()
    UserOrgScopeService().assign(u.id, scope_type="BRANCH", branch_id=branch.id)
    return u


def test_manila_and_cebu_users_see_only_their_own_branch_vehicles(client, db):
    """Reproduces the exact reported scenario: Juan (Manila) and Pedro
    (Cebu) both saw all 3 vehicles including each other's branch-specific
    ones on the Vehicle Master list."""
    hq = BranchService().create(code="BR-HQ-UI", name="Head Office")
    manila = BranchService().create(code="BR-MNL-UI", name="MANILA")
    cebu = BranchService().create(code="BR-CEB-UI", name="Cebu")
    vt = VehicleTypeService().create(code="LV-UISCOPE3", name="Light Vehicle",
                                     category="LIGHT")

    VehicleService().create(vehicle_type_id=vt.id, brand="Toyota", model="Hilux",
                           year=2024, branch_id=hq.id, plate_number="ABC 123")
    manila_vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Honda", model="HRV", year=2020,
        branch_id=manila.id, conduction_number="HRV001")
    cebu_vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Honda", model="City", year=2024,
        branch_id=cebu.id, conduction_number="CITY001")

    juan = _make_scoped_user(client, db, "juan_manila_ui", manila,
                             ["vehicle.view"])
    client.post("/login", data={"username": "juan_manila_ui", "password": "pw123456"})
    resp = client.get("/master/vehicles")
    assert b"HRV001" in resp.data or b"HRV" in resp.data
    assert b"CITY001" not in resp.data
    client.get("/logout")

    pedro = _make_scoped_user(client, db, "pedro_cebu_ui", cebu,
                              ["vehicle.view"])
    client.post("/login", data={"username": "pedro_cebu_ui", "password": "pw123456"})
    resp = client.get("/master/vehicles")
    assert b"CITY001" in resp.data
    assert b"HRV001" not in resp.data

    # Direct URL access to the other branch's vehicle is genuinely blocked
    resp2 = client.get(f"/master/vehicles/{manila_vehicle.id}")
    assert resp2.status_code == 403
