from app.core.security.password import hash_password
from app.modules.user_management.models import User, Role, Permission


def _login(client, db, *, codes=()):
    role = Role(name="PermPickerRole")
    for code in codes:
        m, a = code.split(".")
        p = Permission(code=code, module=m, action=a)
        db.session.add(p)
        role.permissions.append(p)
    u = User(username="farid", email="farid@x.com",
             password_hash=hash_password("pw123456"))
    u.roles.append(role)
    db.session.add_all([role, u])
    db.session.commit()
    client.post("/login", data={"username": "farid", "password": "pw123456"})
    return u


def test_role_new_form_shows_permission_picker(client, db):
    _login(client, db, codes=["role.view", "role.create"])
    resp = client.get("/admin/roles/new")
    assert resp.status_code == 200
    assert b"permSearch" in resp.data
    assert b"permSelectAll" in resp.data
    assert b"permClearAll" in resp.data
    assert b'class="form-check-input perm-checkbox"' in resp.data


def test_role_new_permissions_grouped_by_module(client, db):
    _login(client, db, codes=["role.view", "role.create"])
    resp = client.get("/admin/roles/new")
    assert resp.status_code == 200
    # Real module names from earlier phases should appear as group headers
    assert b"perm-group" in resp.data


def test_create_role_with_checkbox_permissions(client, db):
    _login(client, db, codes=["role.view", "role.create"])
    perm = Permission.query.filter_by(code="role.view").first()
    resp = client.post("/admin/roles/new", data={
        "name": "Picker Test Role",
        "description": "Created via permission picker",
        "permissions": [str(perm.id)],
    }, follow_redirects=True)
    assert resp.status_code == 200
    role = Role.query.filter_by(name="Picker Test Role").first()
    assert role is not None
    assert len(role.permissions) == 1
    assert role.permissions[0].code == "role.view"


def test_edit_role_preselects_existing_permissions(client, db):
    _login(client, db, codes=["role.view", "role.create", "role.update"])
    perm1 = Permission.query.filter_by(code="role.view").first()
    perm2 = Permission.query.filter_by(code="role.create").first()
    from app.extensions import db as _db
    role = Role(name="Preselect Role")
    role.permissions.extend([perm1, perm2])
    _db.session.add(role)
    _db.session.commit()

    resp = client.get(f"/admin/roles/{role.id}/edit")
    assert resp.status_code == 200
    html = resp.data.decode()
    # Both existing permission checkboxes should render as checked
    import re
    for perm in (perm1, perm2):
        match = re.search(
            rf'id="perm_{perm.id}"[^>]*checked', html)
        assert match is not None, f"perm_{perm.id} checkbox not checked"
