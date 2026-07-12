"""Business rules for user/role administration."""
from app.extensions import db
from app.core.security.password import hash_password
from app.modules.user_management.models import Role
from app.modules.user_management.repository import (
    UserRepository, RoleRepository, PermissionRepository)


class DuplicateUsernameError(Exception):
    pass


class SystemRoleError(Exception):
    pass


class UserService:
    def __init__(self):
        self.users = UserRepository()
        self.roles = RoleRepository()

    def create_user(self, username, email, password, first_name=None,
                    last_name=None, role_ids=None, must_change_password=False,
                    employee_id=None, branch_id=None, department_id=None):
        if self.users.get_by_username(username) is not None:
            raise DuplicateUsernameError(f"Username '{username}' already exists.")
        user = self.users.create(
            username=username, email=email,
            password_hash=hash_password(password),
            first_name=first_name, last_name=last_name,
            employee_id=employee_id, branch_id=branch_id,
            department_id=department_id,
            must_change_password=must_change_password)
        self._assign_roles(user, role_ids or [])
        db.session.commit()
        return user

    def update_user(self, user_id, *, email=None, first_name=None,
                    last_name=None, role_ids=None, password=None,
                    employee_id=None, branch_id=None, department_id=None):
        user = self.users.get_by_id(user_id, include_inactive=True)
        if user is None:
            return None
        if email is not None:
            user.email = email
        if first_name is not None:
            user.first_name = first_name
        if last_name is not None:
            user.last_name = last_name
        if employee_id is not None:
            user.employee_id = employee_id
        if branch_id is not None:
            user.branch_id = branch_id
        if department_id is not None:
            user.department_id = department_id
        if password:
            user.password_hash = hash_password(password)
        if role_ids is not None:
            user.roles.clear()
            self._assign_roles(user, role_ids)
        db.session.commit()
        return user

    def deactivate_user(self, user_id):
        self.users.soft_delete(user_id)
        db.session.commit()

    def _assign_roles(self, user, role_ids):
        for rid in role_ids:
            role = self.roles.get_by_id(rid)
            if role is not None:
                user.roles.append(role)
        db.session.flush()


class RoleService:
    def __init__(self):
        self.roles = RoleRepository()
        self.permissions = PermissionRepository()

    def create_role(self, name, description=None, permission_ids=None):
        role = self.roles.create(name=name, description=description)
        self._assign_permissions(role, permission_ids or [])
        db.session.commit()
        return role

    def update_role(self, role_id, *, name=None, description=None,
                    permission_ids=None):
        role = self.roles.get_by_id(role_id)
        if role is None:
            return None
        if name is not None:
            role.name = name
        if description is not None:
            role.description = description
        if permission_ids is not None:
            role.permissions.clear()
            self._assign_permissions(role, permission_ids)
        db.session.commit()
        return role

    def delete_role(self, role_id):
        role = self.roles.get_by_id(role_id)
        if role is None:
            return
        if role.is_system_role:
            raise SystemRoleError("System roles cannot be deleted.")
        self.roles.soft_delete(role_id)
        db.session.commit()

    def _assign_permissions(self, role: Role, permission_ids):
        for pid in permission_ids:
            perm = self.permissions.get_by_id(pid)
            if perm is not None:
                role.permissions.append(perm)
        db.session.flush()
