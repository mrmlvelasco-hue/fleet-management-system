from app.modules.user_management.models import User, Role, Permission


def test_user_role_permission_chain(db):
    perm = Permission(code="user.create", module="user", action="create",
                      description="Create users")
    role = Role(name="Admin", description="Administrators")
    role.permissions.append(perm)
    user = User(username="alice", email="alice@example.com",
                password_hash="x", first_name="Alice", last_name="A")
    user.roles.append(role)
    db.session.add_all([perm, role, user])
    db.session.commit()

    assert user.has_permission("user.create") is True
    assert user.has_permission("user.delete") is False


def test_user_multiple_roles(db):
    r1 = Role(name="Viewer")
    r2 = Role(name="Approver")
    r2.permissions.append(Permission(code="doc.approve", module="doc",
                                     action="approve"))
    u = User(username="bob", email="bob@example.com", password_hash="x")
    u.roles.extend([r1, r2])
    db.session.add(u)
    db.session.commit()
    assert u.has_permission("doc.approve") is True
