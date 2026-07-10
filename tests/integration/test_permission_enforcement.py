from app.core.security.password import hash_password
from app.modules.user_management.models import User, Role, Permission


def _login(client, db, *, permission_codes=()):
    role = Role(name="TestRole")
    for code in permission_codes:
        perm = Permission.query.filter_by(code=code).first()
        if perm is None:
            module, action = code.split(".")
            perm = Permission(code=code, module=module, action=action)
            db.session.add(perm)
        role.permissions.append(perm)
    user = User(username="henry", email="h@example.com",
                password_hash=hash_password("pw123456"))
    user.roles.append(role)
    db.session.add_all([role, user])
    db.session.commit()
    client.post("/login", data={"username": "henry", "password": "pw123456"})
    return user


def test_users_list_403_without_permission(client, db):
    _login(client, db)
    assert client.get("/admin/users").status_code == 403


def test_users_list_200_with_permission(client, db):
    _login(client, db, permission_codes=["user.view"])
    assert client.get("/admin/users").status_code == 200


def test_permissions_seeded_at_startup(app, db):
    from app.core.security.registry import sync_permissions
    sync_permissions()
    db.session.commit()
    assert Permission.query.filter_by(code="user.create").count() == 1
