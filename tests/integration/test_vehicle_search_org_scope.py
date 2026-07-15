from app.core.security.password import hash_password
from app.modules.user_management.models import User, Role, Permission
from app.modules.user_management.org_scope_service import UserOrgScopeService
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.vehicle.service import VehicleService


def _login(client, db, *, codes=()):
    role = Role(name="VehicleSearchScopeRole")
    for code in codes:
        m, a = code.split(".")
        p = Permission.query.filter_by(code=code).first()
        if p is None:
            p = Permission(code=code, module=m, action=a)
            db.session.add(p)
        role.permissions.append(p)
    u = User(username="vehsearch_manila", email="vehsearch_manila@x.com",
             password_hash=hash_password("pw123456"))
    u.roles.append(role)
    db.session.add_all([role, u])
    db.session.commit()
    client.post("/login", data={"username": "vehsearch_manila", "password": "pw123456"})
    return u


def test_vehicle_dropdown_search_hides_other_branch_vehicles(client, db):
    """Reproduces the exact reported bug: a Manila-scoped user creating a
    Maintenance Order could still search up and select vehicles from
    other branches via the AJAX dropdown, even though the Vehicle Master
    list already correctly hid them."""
    manila = BranchService().create(code="BR-VEHSEARCHUI-MNL", name="Manila VehSearch")
    cebu = BranchService().create(code="BR-VEHSEARCHUI-CEB", name="Cebu VehSearch")
    vt = VehicleTypeService().create(code="LV-VEHSEARCHUI", name="Light", category="LIGHT")

    VehicleService().create(vehicle_type_id=vt.id, brand="Toyota", model="Vios",
                           year=2024, branch_id=manila.id,
                           conduction_number="VEHSEARCHUI-MNL-1")
    VehicleService().create(vehicle_type_id=vt.id, brand="Honda", model="City",
                           year=2024, branch_id=cebu.id,
                           conduction_number="VEHSEARCHUI-CEB-1")

    user = _login(client, db, codes=["vehicle.view"])
    UserOrgScopeService().assign(user.id, scope_type="BRANCH", branch_id=manila.id)

    resp = client.get("/api/search/vehicles?q=VEHSEARCHUI")
    data = resp.get_json()
    texts = " ".join(r["text"] for r in data["results"])
    assert "VEHSEARCHUI-MNL-1" in texts
    assert "VEHSEARCHUI-CEB-1" not in texts


def test_vehicle_search_modal_table_hides_other_branch_vehicles(client, db):
    """Same bug, but for the 'Advanced search' modal button (table mode)
    rather than the dropdown — the user reported both had the same leak."""
    manila = BranchService().create(code="BR-VEHSEARCHUI2-MNL", name="Manila VehSearch2")
    cebu = BranchService().create(code="BR-VEHSEARCHUI2-CEB", name="Cebu VehSearch2")
    vt = VehicleTypeService().create(code="LV-VEHSEARCHUI2", name="Light", category="LIGHT")

    VehicleService().create(vehicle_type_id=vt.id, brand="Toyota", model="Innova",
                           year=2024, branch_id=manila.id,
                           conduction_number="VEHSEARCHUI2-MNL-1")
    VehicleService().create(vehicle_type_id=vt.id, brand="Ford", model="Ranger",
                           year=2024, branch_id=cebu.id,
                           conduction_number="VEHSEARCHUI2-CEB-1")

    user = _login(client, db, codes=["vehicle.view"])
    UserOrgScopeService().assign(user.id, scope_type="BRANCH", branch_id=manila.id)

    resp = client.get("/api/search/vehicles/table?q=VEHSEARCHUI2")
    data = resp.get_json()
    plates = [r["plate"] for r in data["rows"]]
    assert "VEHSEARCHUI2-MNL-1" in plates
    assert "VEHSEARCHUI2-CEB-1" not in plates
