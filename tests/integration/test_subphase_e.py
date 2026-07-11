from app.core.security.password import hash_password
from app.modules.user_management.models import User, Role, Permission
from app.modules.master_data.org.service import BranchService


def _login(client, db, *, codes=()):
    role = Role(name="SubphaseERole")
    for code in codes:
        m, a = code.split(".")
        p = Permission(code=code, module=m, action=a)
        db.session.add(p)
        role.permissions.append(p)
    u = User(username="hamid", email="hamid@x.com",
             password_hash=hash_password("pw123456"))
    u.roles.append(role)
    db.session.add_all([role, u])
    db.session.commit()
    client.post("/login", data={"username": "hamid", "password": "pw123456"})
    return u


def test_branch_search_endpoint(client, db):
    _login(client, db, codes=["branch.view"])
    BranchService().create(code="BR-E1", name="Sub-phase E Branch")
    resp = client.get("/api/search/branches?q=Sub-phase")
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data["results"]) == 1
    assert "Sub-phase E Branch" in data["results"][0]["text"]


def test_vehicle_form_uses_ajax_branch_select(client, db):
    _login(client, db, codes=["vehicle.view", "vehicle.create"])
    resp = client.get("/master/vehicles/new")
    assert resp.status_code == 200
    assert b"vehBranchSelect" in resp.data
    assert b"/api/search/branches" in resp.data


def test_driver_form_uses_ajax_branch_select(client, db):
    _login(client, db, codes=["driver.view", "driver.create"])
    resp = client.get("/master/drivers/new")
    assert resp.status_code == 200
    assert b"drvBranchSelect" in resp.data


def test_department_new_form_uses_ajax_branch_select(client, db):
    _login(client, db, codes=["department.view", "department.create"])
    resp = client.get("/master/departments/new")
    assert resp.status_code == 200
    assert b"deptBranchSelect" in resp.data


def test_department_edit_form_shows_disabled_branch(client, db):
    _login(client, db, codes=[
        "department.view", "department.create", "department.update"])
    branch = BranchService().create(code="BR-E2", name="Dept Test Branch")
    resp = client.post("/master/departments/new", data={
        "code": "DEPT-E1", "name": "Test Dept", "branch_id": str(branch.id),
    }, follow_redirects=True)
    assert resp.status_code == 200
    from app.modules.master_data.org.models import Department
    dept = Department.query.filter_by(code="DEPT-E1").first()
    assert dept is not None

    edit_resp = client.get(f"/master/departments/{dept.id}/edit")
    assert edit_resp.status_code == 200
    assert b"disabled" in edit_resp.data
    assert b"Dept Test Branch" in edit_resp.data


def test_vehicleregistration_form_uses_ajax_vehicle_select(client, db):
    _login(client, db, codes=[
        "vehicleregistration.view", "vehicleregistration.create"])
    resp = client.get("/transactions/vehicle-registrations/new")
    assert resp.status_code == 200
    assert b"vrVehicleSelect" in resp.data
    assert b"vrVehicleModalBtn" in resp.data
    assert b"/api/search/vehicles/table" in resp.data
