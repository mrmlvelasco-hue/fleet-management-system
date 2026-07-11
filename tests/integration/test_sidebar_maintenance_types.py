from app.core.security.password import hash_password
from app.modules.user_management.models import User, Role, Permission


def test_maintenance_types_link_appears_in_sidebar(client, db):
    """Regression: Maintenance Types had a working route/permission but was
    never added to the sidebar, making it undiscoverable."""
    role = Role(name="SidebarRole")
    for code in ["maintenancetype.view", "vehicletype.view"]:
        m, a = code.split(".")
        p = Permission(code=code, module=m, action=a)
        db.session.add(p)
        role.permissions.append(p)
    u = User(username="wael", email="wael@x.com",
             password_hash=hash_password("pw123456"))
    u.roles.append(role)
    db.session.add_all([role, u])
    db.session.commit()
    client.post("/login", data={"username": "wael", "password": "pw123456"})

    resp = client.get("/")
    assert resp.status_code == 200
    assert b"Maintenance Types" in resp.data
    assert b'href="/master/maintenance-types"' in resp.data
