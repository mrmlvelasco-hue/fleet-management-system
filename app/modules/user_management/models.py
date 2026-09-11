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
    employee_id = db.Column(db.String(40), nullable=True)
    branch_id = db.Column(db.Integer, db.ForeignKey("branches.id"),
                          nullable=True)
    department_id = db.Column(db.Integer, db.ForeignKey("departments.id"),
                              nullable=True)
    last_login_at = db.Column(db.DateTime, nullable=True)
    failed_login_attempts = db.Column(db.Integer, default=0, nullable=False)
    # Deliberately a per-account flag rather than a hardcoded check
    # against the literal username "admin" -- that would silently break
    # if the account were ever renamed, and would be invisible to
    # anyone reading the authentication code. A real, visible toggle
    # any user.update holder can see and set (or unset) on any account,
    # not just one baked-in exception.
    #
    # The tradeoff is real and worth stating: an exempt account can be
    # brute-forced with unlimited attempts, since the one defence this
    # system has against that is switched off for it. That's the
    # correct trade ONLY for a small number of trusted "break glass"
    # accounts (typically the seeded System Administrator) where being
    # permanently locked out of the one account that can unlock every
    # other account is the worse failure mode -- it should not become
    # the default for ordinary users.
    is_lockout_exempt = db.Column(db.Boolean, default=False, nullable=False)
    must_change_password = db.Column(db.Boolean, default=False, nullable=False)
    # When the current password_hash was set — needed to enforce
    # PASSWORD_EXPIRY_DAYS/PASSWORD_WARNING_DAYS (System Parameters that
    # existed since Phase 1c but were never actually enforced anywhere
    # until this). Nullable: existing users before this column existed
    # have no known change date, so they're treated as "not yet tracked,
    # not expired" rather than force-expiring everyone the moment this
    # ships — the clock starts the next time each of them changes their
    # password.
    password_changed_at = db.Column(db.DateTime, nullable=True)
    # Which sidebar appearance this person chose for themselves.
    # NULL means "no personal choice", which resolves to the
    # company-wide SIDEBAR_SKIN_DEFAULT parameter -- deliberately not
    # the same as storing "classic", because a user who has simply
    # never expressed a preference should follow the house style if
    # the client later changes it, whereas someone who explicitly
    # picked Classic should stay on Classic. See
    # app/core/appearance/skin_service.py.
    sidebar_skin = db.Column(db.String(30), nullable=True)
    # Whether this account may obtain a token from the Android field
    # app. A CHANNEL, not a capability -- which is why it lives here as
    # its own column rather than as a permission code.
    #
    # A permission answers "what may this person do"; roles already do
    # that job well and both channels share them, so a user's phone can
    # never do more than their browser. This answers a different
    # question -- "may this person's phone hold a credential at all" --
    # and the two do not move together. A Fleet Manager may hold every
    # checklist permission and never touch a phone; a driver has mobile
    # access with almost no permissions. Folding the channel into the
    # permission matrix would mean every future role edit silently
    # grants or revokes phone access to everyone holding that role.
    #
    # Kept separate from the drivers.user_id link for the same reason:
    # linking records WHO someone is, this records WHETHER their device
    # may sign in. An assignee can be linked for reporting without being
    # handed the field app, and a lost phone can be cut off without
    # unlinking the person from their vehicle.
    #
    # Defaults to False. An existing install that runs the migration and
    # changes nothing else grants nobody native access -- the closed
    # default is what makes it safe to add a gate to a live endpoint.
    mobile_access = db.Column(db.Boolean, default=False, nullable=False)
    #: Narrow this account to the vehicles CURRENTLY assigned to it.
    #:
    #: Org scope answers "which vehicles may this ROLE see" -- for a
    #: Fleet Officer, correctly the whole branch. This answers "which
    #: vehicles is this PERSON responsible for", which for an assignee
    #: is one or two. The two INTERSECT: switching this on never widens
    #: access beyond the user's branch scope, it only narrows within it.
    #:
    #: Deliberately a per-user flag rather than a check on the "Vehicle
    #: Assignee" role. Keying on a role name would hard-code a value
    #: that is configuration (the role list is maintained in System
    #: Administration), and it would silently change access for anyone
    #: who holds that role alongside another -- a Fleet Officer who also
    #: drives would lose the ability to raise a Maintenance Order for a
    #: vehicle that is not theirs.
    #:
    #: Defaults to False, so an install that runs the migration and
    #: changes nothing else restricts nobody.
    restrict_to_assigned_vehicles = db.Column(db.Boolean, default=False,
                                              nullable=False)
    roles = db.relationship("Role", secondary=user_roles, backref="users")
    branch = db.relationship("Branch", foreign_keys=[branch_id])
    department = db.relationship("Department")

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


class PasswordHistory(db.Model, BaseModel):
    """Enforces PASSWORD_HISTORY_LENGTH — a System Parameter that existed
    since Phase 1c but had nothing recording past hashes to check
    against until this. One row per password a user has ever had; only
    the most recent PASSWORD_HISTORY_LENGTH rows are actually checked
    (old ones beyond that are pruned on each change, not kept forever)."""
    __tablename__ = "password_histories"
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False,
                        index=True)
    password_hash = db.Column(db.String(255), nullable=False)

    user = db.relationship("User", backref="password_history")


@login_manager.user_loader
def load_user(user_id: str):
    return db.session.get(User, int(user_id))
