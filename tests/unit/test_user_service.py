import pytest

from app.modules.user_management.models import Role, User
from app.modules.user_management.service import (
    UserService, RoleService, DuplicateUsernameError, SystemRoleError)


def test_create_user_hashes_password_and_assigns_roles(db):
    role = Role(name="Clerk")
    db.session.add(role)
    db.session.commit()
    svc = UserService()
    u = svc.create_user(username="dora", email="d@example.com",
                        password="secret123", first_name="Dora",
                        last_name="D", role_ids=[role.id])
    assert u.password_hash != "secret123"
    assert [r.name for r in u.roles] == ["Clerk"]


def test_duplicate_username_rejected(db):
    svc = UserService()
    svc.create_user(username="eve", email="e1@example.com", password="x" * 8)
    with pytest.raises(DuplicateUsernameError):
        svc.create_user(username="eve", email="e2@example.com", password="x" * 8)


def test_deactivate_user(db):
    svc = UserService()
    u = svc.create_user(username="finn", email="f@example.com", password="x" * 8)
    svc.deactivate_user(u.id)
    assert db.session.get(User, u.id).is_active is False


def test_system_role_cannot_be_deleted(db):
    role = Role(name="SuperAdmin", is_system_role=True)
    db.session.add(role)
    db.session.commit()
    with pytest.raises(SystemRoleError):
        RoleService().delete_role(role.id)
