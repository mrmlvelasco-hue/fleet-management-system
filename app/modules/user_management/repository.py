"""Repositories for User, Role, Permission."""
from app.core.repository.base_repository import BaseRepository
from app.modules.user_management.models import User, Role, Permission


class UserRepository(BaseRepository):
    model = User

    def get_by_username(self, username: str, include_inactive: bool = True):
        return User.query.filter_by(username=username).first() \
            if include_inactive else \
            User.query.filter_by(username=username, is_active=True).first()


class RoleRepository(BaseRepository):
    model = Role


class PermissionRepository(BaseRepository):
    model = Permission
