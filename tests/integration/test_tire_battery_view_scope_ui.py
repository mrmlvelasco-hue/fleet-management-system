from app.core.security.password import hash_password
from app.modules.user_management.models import User, Role, Permission
from app.modules.user_management.org_scope_service import UserOrgScopeService
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.tire.service import TireService
from app.modules.master_data.battery.service import BatteryService


def _login(client, db, *, codes=()):
    role = Role(name="TireBattScopeRole")
    for code in codes:
        m, a = code.split(".")
        p = Permission.query.filter_by(code=code).first()
        if p is None:
            p = Permission(code=code, module=m, action=a)
            db.session.add(p)
        role.permissions.append(p)
    u = User(username="tirebatt_ui", email="tirebatt_ui@x.com",
             password_hash=hash_password("pw123456"))
    u.roles.append(role)
    db.session.add_all([role, u])
    db.session.commit()
    client.post("/login", data={"username": "tirebatt_ui", "password": "pw123456"})
    return u


def test_tire_form_has_branch_field(client, db):
    _login(client, db, codes=["tire.view", "tire.create"])
    resp = client.get("/master/tires/new")
    assert resp.status_code == 200
    assert b'id="tireBranchSelect"' in resp.data


def test_battery_form_has_branch_field(client, db):
    _login(client, db, codes=["battery.view", "battery.create"])
    resp = client.get("/master/batteries/new")
    assert resp.status_code == 200
    assert b'id="battBranchSelect"' in resp.data


def test_create_tire_with_branch(client, db):
    _login(client, db, codes=["tire.view", "tire.create"])
    branch = BranchService().create(code="BR-TIREUI", name="Tire UI Branch")
    resp = client.post("/master/tires/new", data={
        "serial_number": "TIRE-UI-001", "brand": "Bridgestone",
        "size": "185/65R15", "tire_type": "RADIAL",
        "branch_id": str(branch.id),
    }, follow_redirects=True)
    assert resp.status_code == 200
    from app.modules.master_data.tire.models import Tire
    tire = Tire.query.filter_by(serial_number="TIRE-UI-001").first()
    assert tire is not None
    assert tire.branch_id == branch.id


def test_scoped_user_does_not_see_other_branch_tire_in_list(client, db):
    branch_mine = BranchService().create(code="BR-TIREUI2", name="Tire UI Branch 2")
    branch_other = BranchService().create(code="BR-TIREUI3", name="Tire UI Branch 3")
    user = _login(client, db, codes=["tire.view"])
    UserOrgScopeService().assign(user.id, scope_type="BRANCH",
                                branch_id=branch_mine.id)

    TireService().create(serial_number="TIRE-MINE", brand="Bridgestone",
                        size="185/65R15", tire_type="RADIAL",
                        branch_id=branch_mine.id)
    TireService().create(serial_number="TIRE-OTHER", brand="Michelin",
                        size="195/65R15", tire_type="RADIAL",
                        branch_id=branch_other.id)

    resp = client.get("/master/tires")
    assert b"TIRE-MINE" in resp.data
    assert b"TIRE-OTHER" not in resp.data
