from datetime import date

from app.core.security.password import hash_password
from app.modules.user_management.models import User, Role, Permission
from app.modules.master_data.org.service import BranchService


def _login(client, db, *, codes=()):
    role = Role(name="AssigneeFormUIRole")
    for code in codes:
        m, a = code.split(".")
        p = Permission.query.filter_by(code=code).first()
        if p is None:
            p = Permission(code=code, module=m, action=a)
            db.session.add(p)
        role.permissions.append(p)
    u = User(username="assignee_form_ui_user", email="assignee_form_ui_user@x.com",
             password_hash=hash_password("pw123456"))
    u.roles.append(role)
    db.session.add_all([role, u])
    db.session.commit()
    client.post("/login", data={"username": "assignee_form_ui_user", "password": "pw123456"})
    return u


def test_new_form_shows_all_sections(client, db):
    _login(client, db, codes=["driver.view", "driver.create"])
    resp = client.get("/master/drivers/new")
    assert resp.status_code == 200
    assert b"Basic Information" in resp.data
    assert b"Organization Information" in resp.data
    assert b"License Information" in resp.data
    assert b"Contact Information" in resp.data
    assert b"Business Information" in resp.data
    assert b"Third Party Delivery" in resp.data


def test_create_employee_assignee_without_license(client, db):
    """The exact rule from the spec: a non-Driver assignee must be
    createable through the actual form without license fields."""
    _login(client, db, codes=["driver.view", "driver.create"])
    branch = BranchService().create(code="BR-ASSIGNEEUI", name="Assignee UI Branch")
    resp = client.post("/master/drivers/new", data={
        "employee_number": "EMP-ASSIGNEEUI1", "assignee_type": "EMPLOYEE",
        "first_name": "Maria", "last_name": "Santos", "branch_id": str(branch.id),
    }, follow_redirects=True)
    assert resp.status_code == 200

    from app.modules.master_data.driver.models import Driver
    person = Driver.query.filter_by(employee_number="EMP-ASSIGNEEUI1").first()
    assert person is not None
    assert person.assignee_type == "EMPLOYEE"
    assert person.license_number is None
    assert person.person_id is not None


def test_create_driver_type_still_requires_license(client, db):
    _login(client, db, codes=["driver.view", "driver.create"])
    branch = BranchService().create(code="BR-ASSIGNEEUI2", name="Assignee UI Branch 2")
    resp = client.post("/master/drivers/new", data={
        "employee_number": "EMP-ASSIGNEEUI2", "assignee_type": "DRIVER",
        "first_name": "Juan", "last_name": "Cruz", "branch_id": str(branch.id),
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert b"required" in resp.data.lower() or b"license" in resp.data.lower()

    from app.modules.master_data.driver.models import Driver
    person = Driver.query.filter_by(employee_number="EMP-ASSIGNEEUI2").first()
    assert person is None  # creation must have been rejected


def test_add_emergency_contact_via_form(client, db):
    _login(client, db, codes=["driver.view", "driver.create", "driver.update"])
    branch = BranchService().create(code="BR-ASSIGNEEUI3", name="Assignee UI Branch 3")
    client.post("/master/drivers/new", data={
        "employee_number": "EMP-ASSIGNEEUI3", "assignee_type": "EMPLOYEE",
        "first_name": "Test", "last_name": "Person", "branch_id": str(branch.id),
    })
    from app.modules.master_data.driver.models import Driver
    person = Driver.query.filter_by(employee_number="EMP-ASSIGNEEUI3").first()

    resp = client.post(f"/master/drivers/{person.id}/emergency-contacts", data={
        "contact_name": "Jane Doe", "relationship_type": "Spouse",
        "contact_number": "0917-000-1111",
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert b"Jane Doe" in resp.data
    assert b"Spouse" in resp.data
