from app.core.security.password import hash_password
from app.modules.user_management.models import User, Role, Permission
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.vehicle.service import VehicleService


def _login(client, db, *, codes=()):
    role = Role(name="ModalRole")
    for code in codes:
        m, a = code.split(".")
        p = Permission(code=code, module=m, action=a)
        db.session.add(p)
        role.permissions.append(p)
    u = User(username="omar2", email="omar2@x.com",
             password_hash=hash_password("pw123456"))
    u.roles.append(role)
    db.session.add_all([role, u])
    db.session.commit()
    client.post("/login", data={"username": "omar2", "password": "pw123456"})
    return u


def test_vehicle_table_endpoint_requires_permission(client, db):
    _login(client, db)
    resp = client.get("/api/search/vehicles/table")
    assert resp.status_code == 403


def test_vehicle_table_response_shape(client, db):
    _login(client, db, codes=["vehicle.view"])
    branch = BranchService().create(code="BR-MODAL", name="Modal Branch")
    vt = VehicleTypeService().create(code="LV-MODAL", name="Light", category="LIGHT")
    VehicleService().create(vehicle_type_id=vt.id, brand="Toyota", model="Hilux",
                            year=2024, branch_id=branch.id,
                            conduction_number="MODAL-001")
    resp = client.get("/api/search/vehicles/table")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "rows" in data and "total" in data and "total_pages" in data
    assert data["rows"][0]["brand"] == "Toyota"
    assert data["rows"][0]["branch"] == "Modal Branch"


def test_vehicle_table_sort_by_brand(client, db):
    _login(client, db, codes=["vehicle.view"])
    branch = BranchService().create(code="BR-MODAL2", name="Modal Branch 2")
    vt = VehicleTypeService().create(code="LV-MODAL2", name="Light", category="LIGHT")
    VehicleService().create(vehicle_type_id=vt.id, brand="Zeta", model="X",
                            year=2024, branch_id=branch.id,
                            conduction_number="MODAL-Z")
    VehicleService().create(vehicle_type_id=vt.id, brand="Alpha", model="Y",
                            year=2024, branch_id=branch.id,
                            conduction_number="MODAL-A")
    resp = client.get("/api/search/vehicles/table?sort_by=brand&sort_dir=asc")
    data = resp.get_json()
    brands = [r["brand"] for r in data["rows"]]
    assert brands.index("Alpha") < brands.index("Zeta")


def test_vehicle_table_filter_by_branch(client, db):
    _login(client, db, codes=["vehicle.view"])
    branch1 = BranchService().create(code="BR-MF1", name="Filter Branch 1")
    branch2 = BranchService().create(code="BR-MF2", name="Filter Branch 2")
    vt = VehicleTypeService().create(code="LV-MF", name="Light", category="LIGHT")
    VehicleService().create(vehicle_type_id=vt.id, brand="Toyota", model="Vios",
                            year=2024, branch_id=branch1.id,
                            conduction_number="MF-001")
    VehicleService().create(vehicle_type_id=vt.id, brand="Honda", model="City",
                            year=2024, branch_id=branch2.id,
                            conduction_number="MF-002")
    resp = client.get(f"/api/search/vehicles/table?branch_id={branch1.id}")
    data = resp.get_json()
    assert data["total"] == 1
    assert data["rows"][0]["brand"] == "Toyota"


def test_vehicle_table_pagination(client, db):
    _login(client, db, codes=["vehicle.view"])
    branch = BranchService().create(code="BR-MP", name="Pagination Branch")
    vt = VehicleTypeService().create(code="LV-MP", name="Light", category="LIGHT")
    for i in range(25):
        VehicleService().create(vehicle_type_id=vt.id, brand="Toyota",
                                model="Vios", year=2024, branch_id=branch.id,
                                conduction_number=f"MP-{i:03d}")
    resp = client.get("/api/search/vehicles/table?q=MP&page=1&per_page=10")
    data = resp.get_json()
    assert len(data["rows"]) == 10
    assert data["total"] == 25
    assert data["total_pages"] == 3
