"""User, Role, Permission models and their association tables (RBAC core)."""
from flask_login import UserMixin

from app.extensions import db, login_manager
from app.core.models.base import BaseModel

user_roles = db.Table(
    "user_roles",
    db.Column("user_id", db.Integer, db.ForeignKey("users.id"), primary_key=True),
    db.Column("role_id", db.Integer, db.ForeignKey("roles.id"), primary_key=True),
)

role_permissions = db.Table(
    "role_permissions",
    db.Column("role_id", db.Integer, db.ForeignKey("roles.id"), primary_key=True),
    db.Column("permission_id", db.Integer, db.ForeignKey("permissions.id"),
              primary_key=True),
)


class Permission(db.Model, BaseModel):
    __tablename__ = "permissions"
    code = db.Column(db.String(100), unique=True, nullable=False, index=True)
    module = db.Column(db.String(50), nullable=False)
    action = db.Column(db.String(50), nullable=False)
    description = db.Column(db.String(255))


class Role(db.Model, BaseModel):
    __tablename__ = "roles"
    name = db.Column(db.String(80), unique=True, nullable=False)
    description = db.Column(db.String(255))
    is_system_role = db.Column(db.Boolean, default=False, nullable=False)
    permissions = db.relationship("Permission", secondary=role_permissions,
                                  backref="roles")


class User(db.Model, BaseModel, UserMixin):
    __tablename__ = "users"
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    # Not unique: login is by username, not email — duplicate emails are
    # harmless here (e.g. shared test accounts, multiple placeholder
    # addresses) and previously caused an unhandled IntegrityError crash.
    email = db.Column(db.String(255), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    first_name = db.Column(db.String(80))
    last_name = db.Column(db.String(80))
    branch_id = db.Column(db.Integer, nullable=True)  # FK added with Branch master (Phase 2)
    last_login_at = db.Column(db.DateTime, nullable=True)
    failed_login_attempts = db.Column(db.Integer, default=0, nullable=False)
    must_change_password = db.Column(db.Boolean, default=False, nullable=False)
    roles = db.relationship("Role", secondary=user_roles, backref="users")

    def has_permission(self, code: str) -> bool:
        return any(
            perm.code == code
            for role in self.roles if role.is_active
            for perm in role.permissions if perm.is_active
        )

    @property
    def full_name(self) -> str:
        return f"{self.first_name or ''} {self.last_name or ''}".strip() or self.username


class UserOrgScope(db.Model, BaseModel):
    """Organizational scope a user is authorized to act within for
    approval purposes — independent of Role, since the same Role (e.g.
    "Fleet Manager") may be held by different users in different branches.
    A user can have multiple scope rows (multi-branch access)."""
    __tablename__ = "user_org_scopes"
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    # BRANCH | BUSINESS_UNIT | COMPANY | GLOBAL
    scope_type = db.Column(db.String(20), nullable=False)
    branch_id = db.Column(db.Integer, db.ForeignKey("branches.id"),
                          nullable=True)
    business_unit_id = db.Column(db.Integer,
                                 db.ForeignKey("business_units.id"),
                                 nullable=True)

    user = db.relationship("User", backref="org_scopes")
    branch = db.relationship("Branch")
    business_unit = db.relationship("BusinessUnit")


@login_manager.user_loader
def load_user(user_id: str):
    return db.session.get(User, int(user_id))
