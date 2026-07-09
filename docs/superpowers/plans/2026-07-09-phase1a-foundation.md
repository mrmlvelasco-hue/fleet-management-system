# Phase 1a — Foundation (Architecture, Auth & RBAC) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the FMS foundation: Flask app factory, environment config, BaseModel/BaseRepository, User/Role/Permission RBAC, auth (login/logout/lockout), automatic audit trail, Enterprise UI shell, seed CLI, tests.

**Architecture:** Feature-first modules (`app/modules/<feature>/`) over a `core/` package holding cross-cutting infrastructure (base model, base repository, audit, security). Session auth via Flask-Login; permissions enforced with a `@require_permission` decorator backed by a startup-seeded `PermissionRegistry`. SQLite for dev/test, MySQL-compatible types for prod.

**Tech Stack:** Python 3.13 (3.11+ acceptable in container), Flask 3, SQLAlchemy 2, Flask-Migrate, Flask-Login, Flask-WTF, Marshmallow, Celery+Redis (wired, unused), Argon2 (argon2-cffi), Bootstrap 5 + DataTables + Select2 + SweetAlert2 (CDN), pytest.

**Spec:** `docs/superpowers/specs/2026-07-09-phase1a-foundation-design.md`

---

## File Map

| File | Responsibility |
|---|---|
| `requirements.txt`, `.env.example`, `.gitignore`, `wsgi.py`, `celery_worker.py` | Project scaffold |
| `app/config.py` | Dev/Test/Prod config classes, env-driven |
| `app/extensions.py` | db, migrate, login_manager, csrf, celery singletons |
| `app/__init__.py` | `create_app()` factory: init extensions, register blueprints, error handlers, logging, permission seeding, audit hooks |
| `app/core/models/base.py` | `BaseModel` mixin (id, timestamps, created_by/updated_by, is_active) |
| `app/core/models/audit_log.py` | `AuditLog` model |
| `app/core/repository/base_repository.py` | Generic CRUD + soft delete |
| `app/core/security/password.py` | Argon2 hash/verify |
| `app/core/security/decorators.py` | `@require_permission` |
| `app/core/security/registry.py` | `PermissionRegistry` (code → module/action/description), `sync_permissions()` |
| `app/core/audit/audit_service.py` | SQLAlchemy `before_flush` listener writing AuditLog rows |
| `app/core/celery_app.py` | Celery factory bound to Flask app context |
| `app/modules/user_management/models.py` | User, Role, Permission + association tables |
| `app/modules/user_management/repository.py` | UserRepository, RoleRepository, PermissionRepository |
| `app/modules/user_management/service.py` | UserService, RoleService (business rules; no logic in routes) |
| `app/modules/user_management/schemas.py` | Marshmallow schemas |
| `app/modules/user_management/forms.py` | WTForms for user/role CRUD |
| `app/modules/user_management/routes.py` | Blueprint: /admin/users, /admin/roles, /admin/permissions |
| `app/modules/auth/forms.py`, `routes.py` | Login/logout/change-password |
| `app/modules/auth/service.py` | AuthService: authenticate, lockout, last_login |
| `app/templates/layout/base.html`, `sidebar.html`, `topnav.html` | Enterprise UI shell |
| `app/templates/errors/{403,404,500}.html` | Error pages |
| `app/modules/*/templates/*` | Feature pages |
| `app/static/css/app.css`, `app/static/js/app.js` | Shell styling (incl. dark mode), helpers |
| `app/cli.py` | `flask seed` commands |
| `tests/conftest.py` | app/db fixtures (in-memory SQLite) |
| `tests/unit/*`, `tests/integration/*` | Tests per task |

Conventions used throughout:
- All models inherit `db.Model` + `BaseModel` mixin.
- All repositories subclass `BaseRepository`.
- Routes call services only; services call repositories; repositories touch the session.
- Session flush/commit happens at the service boundary (`BaseRepository` methods flush, services commit).

---

### Task 1: Project scaffold, config, extensions, app factory

**Files:**
- Create: `requirements.txt`, `.gitignore`, `.env.example`, `wsgi.py`, `celery_worker.py`
- Create: `app/__init__.py`, `app/config.py`, `app/extensions.py`, `app/core/__init__.py`, `app/core/celery_app.py`, `app/modules/__init__.py`
- Test: `tests/conftest.py`, `tests/unit/test_app_factory.py`

- [ ] **Step 1: Create requirements.txt**

```
Flask>=3.0
Flask-SQLAlchemy>=3.1
Flask-Migrate>=4.0
Flask-Login>=0.6
Flask-WTF>=1.2
WTForms>=3.1
email-validator>=2.0
marshmallow>=3.20
SQLAlchemy>=2.0
celery>=5.3
redis>=5.0
argon2-cffi>=23.1
python-dotenv>=1.0
pytest>=8.0
```

- [ ] **Step 2: Create .gitignore**

```
__pycache__/
*.pyc
.env
instance/
.pytest_cache/
*.egg-info/
venv/
.venv/
```

- [ ] **Step 3: Create .env.example**

```
FLASK_ENV=development
SECRET_KEY=change-me-in-production
DATABASE_URL=sqlite:///fms_dev.db
REDIS_URL=redis://localhost:6379/0
SESSION_TIMEOUT_MINUTES=30
MAX_FAILED_LOGIN_ATTEMPTS=5
```

- [ ] **Step 4: Create app/config.py**

```python
"""Environment-based configuration classes for the FMS application.

Select via FLASK_ENV (development / testing / production). All values are
read from environment variables so nothing is hardcoded per deployment.
NOTE (spec): SESSION_TIMEOUT / lockout threshold move to the System
Parameters module in Phase 1c; env vars are the 1a interim mechanism.
"""
import os
from datetime import timedelta


class BaseConfig:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL", "sqlite:///fms_dev.db")
    PERMANENT_SESSION_LIFETIME = timedelta(
        minutes=int(os.environ.get("SESSION_TIMEOUT_MINUTES", "30"))
    )
    MAX_FAILED_LOGIN_ATTEMPTS = int(os.environ.get("MAX_FAILED_LOGIN_ATTEMPTS", "5"))
    CELERY_BROKER_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    CELERY_RESULT_BACKEND = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    WTF_CSRF_ENABLED = True
    REMEMBER_COOKIE_HTTPONLY = True
    SESSION_COOKIE_HTTPONLY = True


class DevelopmentConfig(BaseConfig):
    DEBUG = True


class TestingConfig(BaseConfig):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite://"  # in-memory
    WTF_CSRF_ENABLED = False


class ProductionConfig(BaseConfig):
    DEBUG = False
    SESSION_COOKIE_SECURE = True
    REMEMBER_COOKIE_SECURE = True


CONFIG_MAP = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}
```

- [ ] **Step 5: Create app/extensions.py**

```python
"""Flask extension singletons, initialised in the app factory."""
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager
from flask_wtf import CSRFProtect

db = SQLAlchemy()
migrate = Migrate()
login_manager = LoginManager()
csrf = CSRFProtect()

login_manager.login_view = "auth.login"
login_manager.login_message_category = "warning"
```

- [ ] **Step 6: Create app/core/celery_app.py**

```python
"""Celery factory. Wired now (empty queue); Notification Engine uses it later."""
from celery import Celery

celery = Celery("fms")


def init_celery(app):
    """Bind Celery config to the Flask app and run tasks in app context."""
    celery.conf.broker_url = app.config["CELERY_BROKER_URL"]
    celery.conf.result_backend = app.config["CELERY_RESULT_BACKEND"]

    class ContextTask(celery.Task):
        def __call__(self, *args, **kwargs):
            with app.app_context():
                return self.run(*args, **kwargs)

    celery.Task = ContextTask
    return celery
```

- [ ] **Step 7: Create empty packages**

Create `app/core/__init__.py` and `app/modules/__init__.py`, each containing only a module docstring (e.g. `"""Cross-cutting engines and shared infrastructure."""`).

- [ ] **Step 8: Create app/__init__.py (app factory, minimal for now)**

```python
"""FMS application factory."""
import logging
import os
from logging.handlers import RotatingFileHandler

from flask import Flask

from app.config import CONFIG_MAP
from app.extensions import db, migrate, login_manager, csrf
from app.core.celery_app import init_celery


def create_app(config_name: str | None = None) -> Flask:
    config_name = config_name or os.environ.get("FLASK_ENV", "development")
    app = Flask(__name__)
    app.config.from_object(CONFIG_MAP[config_name])

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)
    init_celery(app)
    _configure_logging(app)

    # Blueprints, error handlers, permission sync and audit hooks are
    # registered in later tasks; this factory grows as tasks land.
    return app


def _configure_logging(app: Flask) -> None:
    if app.testing:
        return
    os.makedirs("instance/logs", exist_ok=True)
    handler = RotatingFileHandler(
        "instance/logs/fms.log", maxBytes=1_000_000, backupCount=5
    )
    handler.setFormatter(
        logging.Formatter(
            '{"time":"%(asctime)s","level":"%(levelname)s",'
            '"logger":"%(name)s","message":"%(message)s"}'
        )
    )
    app.logger.addHandler(handler)
    app.logger.setLevel(logging.INFO)
```

- [ ] **Step 9: Create wsgi.py and celery_worker.py**

`wsgi.py`:
```python
"""WSGI entrypoint: `flask --app wsgi run` or gunicorn `wsgi:app`."""
from app import create_app

app = create_app()
```

`celery_worker.py`:
```python
"""Celery worker entrypoint: `celery -A celery_worker.celery worker`."""
from app import create_app
from app.core.celery_app import celery

app = create_app()
```

- [ ] **Step 10: Write conftest and failing factory test**

`tests/conftest.py`:
```python
import pytest

from app import create_app
from app.extensions import db as _db


@pytest.fixture()
def app():
    app = create_app("testing")
    with app.app_context():
        _db.create_all()
        yield app
        _db.session.remove()
        _db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def db(app):
    return _db
```

`tests/unit/test_app_factory.py`:
```python
def test_app_factory_creates_testing_app(app):
    assert app.testing is True
    assert app.config["SQLALCHEMY_DATABASE_URI"] == "sqlite://"
```

- [ ] **Step 11: Install deps and run tests**

Run: `cd /home/claude/fms && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt -q && .venv/bin/pytest tests -v`
Expected: `test_app_factory_creates_testing_app PASSED`

- [ ] **Step 12: Commit**

```bash
git add -A && git commit -m "feat: project scaffold, config, extensions, app factory"
```

---

### Task 2: BaseModel mixin

**Files:**
- Create: `app/core/models/__init__.py`, `app/core/models/base.py`
- Test: `tests/unit/test_base_model.py`

- [ ] **Step 1: Write failing test**

`tests/unit/test_base_model.py`:
```python
from datetime import datetime, timezone

from app.extensions import db
from app.core.models.base import BaseModel


class Widget(db.Model, BaseModel):
    """Throwaway model used only for exercising the mixin."""
    __tablename__ = "test_widget"
    name = db.Column(db.String(50))


def test_base_model_columns(db):
    w = Widget(name="a")
    db.session.add(w)
    db.session.commit()
    assert w.id is not None
    assert isinstance(w.created_at, datetime)
    assert w.is_active is True


def test_soft_delete_flag(db):
    w = Widget(name="b")
    db.session.add(w)
    db.session.commit()
    w.is_active = False
    db.session.commit()
    assert Widget.query.filter_by(is_active=True).count() == 0
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/unit/test_base_model.py -v`
Expected: FAIL / ERROR with `ModuleNotFoundError: app.core.models`

- [ ] **Step 3: Implement mixin**

`app/core/models/__init__.py`: docstring only.

`app/core/models/base.py`:
```python
"""BaseModel mixin: shared audit columns and soft-delete flag.

Every FMS table inherits this so audit columns are uniform and hard
deletes are avoided (Master Data must retain full history).
"""
from datetime import datetime, timezone

from app.extensions import db


def utcnow():
    return datetime.now(timezone.utc)


class BaseModel:
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)
    created_by = db.Column(db.Integer, nullable=True)  # user id; FK omitted to avoid circular deps
    updated_by = db.Column(db.Integer, nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
```

- [ ] **Step 4: Run tests to verify pass**

Run: `.venv/bin/pytest tests/unit/test_base_model.py -v`
Expected: 2 PASSED

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: BaseModel mixin with audit columns and soft delete"
```

---

### Task 3: BaseRepository

**Files:**
- Create: `app/core/repository/__init__.py`, `app/core/repository/base_repository.py`
- Test: `tests/unit/test_base_repository.py`

- [ ] **Step 1: Write failing tests**

`tests/unit/test_base_repository.py`:
```python
from app.extensions import db
from app.core.models.base import BaseModel
from app.core.repository.base_repository import BaseRepository


class Gadget(db.Model, BaseModel):
    __tablename__ = "test_gadget"
    name = db.Column(db.String(50))


class GadgetRepository(BaseRepository):
    model = Gadget


def test_create_and_get(db):
    repo = GadgetRepository()
    g = repo.create(name="g1")
    db.session.commit()
    assert repo.get_by_id(g.id).name == "g1"


def test_list_excludes_soft_deleted(db):
    repo = GadgetRepository()
    g1 = repo.create(name="a")
    g2 = repo.create(name="b")
    db.session.commit()
    repo.soft_delete(g2.id)
    db.session.commit()
    names = [g.name for g in repo.list()]
    assert names == ["a"]


def test_update(db):
    repo = GadgetRepository()
    g = repo.create(name="old")
    db.session.commit()
    repo.update(g.id, name="new")
    db.session.commit()
    assert repo.get_by_id(g.id).name == "new"


def test_list_with_filters(db):
    repo = GadgetRepository()
    repo.create(name="x")
    repo.create(name="y")
    db.session.commit()
    assert [g.name for g in repo.list(name="y")] == ["y"]
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/unit/test_base_repository.py -v`
Expected: ERROR `ModuleNotFoundError: app.core.repository`

- [ ] **Step 3: Implement**

`app/core/repository/__init__.py`: docstring only.

`app/core/repository/base_repository.py`:
```python
"""Generic repository base class (Repository Pattern).

Subclasses set `model`. Methods flush (so ids are assigned) but do NOT
commit; the owning service commits the unit of work.
"""
from app.extensions import db


class BaseRepository:
    model = None  # subclasses must set

    def get_by_id(self, record_id: int, include_inactive: bool = False):
        obj = db.session.get(self.model, record_id)
        if obj is None:
            return None
        if not include_inactive and not obj.is_active:
            return None
        return obj

    def list(self, include_inactive: bool = False, **filters):
        query = db.session.query(self.model)
        if not include_inactive:
            query = query.filter(self.model.is_active.is_(True))
        for attr, value in filters.items():
            query = query.filter(getattr(self.model, attr) == value)
        return query.order_by(self.model.id).all()

    def create(self, **kwargs):
        obj = self.model(**kwargs)
        db.session.add(obj)
        db.session.flush()
        return obj

    def update(self, record_id: int, **kwargs):
        obj = self.get_by_id(record_id)
        if obj is None:
            return None
        for attr, value in kwargs.items():
            setattr(obj, attr, value)
        db.session.flush()
        return obj

    def soft_delete(self, record_id: int):
        obj = self.get_by_id(record_id)
        if obj is None:
            return None
        obj.is_active = False
        db.session.flush()
        return obj
```

- [ ] **Step 4: Run tests to verify pass**

Run: `.venv/bin/pytest tests/unit/test_base_repository.py -v`
Expected: 4 PASSED

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: generic BaseRepository with soft delete and filter hooks"
```

---

### Task 4: User / Role / Permission models

**Files:**
- Create: `app/modules/user_management/__init__.py`, `app/modules/user_management/models.py`
- Test: `tests/unit/test_user_models.py`

- [ ] **Step 1: Write failing tests**

`tests/unit/test_user_models.py`:
```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/unit/test_user_models.py -v`
Expected: ERROR `ModuleNotFoundError: app.modules.user_management`

- [ ] **Step 3: Implement models**

`app/modules/user_management/__init__.py`: docstring only.

`app/modules/user_management/models.py`:
```python
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
    email = db.Column(db.String(255), unique=True, nullable=False)
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


@login_manager.user_loader
def load_user(user_id: str):
    return db.session.get(User, int(user_id))
```

- [ ] **Step 4: Run tests to verify pass**

Run: `.venv/bin/pytest tests/unit/test_user_models.py -v`
Expected: 2 PASSED

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: User/Role/Permission models with RBAC chain"
```

---

### Task 5: Password hashing (Argon2)

**Files:**
- Create: `app/core/security/__init__.py`, `app/core/security/password.py`
- Test: `tests/unit/test_password.py`

- [ ] **Step 1: Write failing test**

`tests/unit/test_password.py`:
```python
from app.core.security.password import hash_password, verify_password


def test_hash_and_verify_roundtrip():
    h = hash_password("s3cret!")
    assert h != "s3cret!"
    assert verify_password(h, "s3cret!") is True
    assert verify_password(h, "wrong") is False
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/unit/test_password.py -v`
Expected: ERROR `ModuleNotFoundError: app.core.security`

- [ ] **Step 3: Implement**

`app/core/security/__init__.py`: docstring only.

`app/core/security/password.py`:
```python
"""Argon2 password hashing helpers."""
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError

_hasher = PasswordHasher()


def hash_password(plain: str) -> str:
    return _hasher.hash(plain)


def verify_password(stored_hash: str, plain: str) -> bool:
    try:
        return _hasher.verify(stored_hash, plain)
    except (VerifyMismatchError, InvalidHashError):
        return False
```

- [ ] **Step 4: Run tests to verify pass**

Run: `.venv/bin/pytest tests/unit/test_password.py -v`
Expected: 1 PASSED

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: Argon2 password hashing helpers"
```

---

### Task 6: PermissionRegistry + require_permission decorator

**Files:**
- Create: `app/core/security/registry.py`, `app/core/security/decorators.py`
- Modify: `app/__init__.py` (call `sync_permissions` after `db` ready — done in Task 9 when blueprints register; registry itself testable standalone)
- Test: `tests/unit/test_registry.py`, `tests/integration/test_permission_enforcement.py` (integration part lands in Task 9 once routes exist)

- [ ] **Step 1: Write failing registry test**

`tests/unit/test_registry.py`:
```python
from app.core.security.registry import PermissionRegistry, sync_permissions
from app.modules.user_management.models import Permission


def test_register_and_sync(db):
    reg = PermissionRegistry()
    reg.register("widget.create", "widget", "create", "Create widgets")
    reg.register("widget.view", "widget", "view", "View widgets")
    sync_permissions(reg)
    db.session.commit()
    codes = {p.code for p in Permission.query.all()}
    assert {"widget.create", "widget.view"} <= codes


def test_sync_is_idempotent(db):
    reg = PermissionRegistry()
    reg.register("widget.create", "widget", "create", "Create widgets")
    sync_permissions(reg)
    sync_permissions(reg)
    db.session.commit()
    assert Permission.query.filter_by(code="widget.create").count() == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/unit/test_registry.py -v`
Expected: ERROR `ModuleNotFoundError ... registry`

- [ ] **Step 3: Implement registry**

`app/core/security/registry.py`:
```python
"""PermissionRegistry: modules declare their permission codes in code;
sync_permissions() upserts them into the DB at startup so DB permissions
never drift from what the code enforces."""
from dataclasses import dataclass


@dataclass(frozen=True)
class PermissionDef:
    code: str
    module: str
    action: str
    description: str = ""


class PermissionRegistry:
    def __init__(self):
        self._defs: dict[str, PermissionDef] = {}

    def register(self, code: str, module: str, action: str, description: str = ""):
        self._defs[code] = PermissionDef(code, module, action, description)

    @property
    def definitions(self) -> list[PermissionDef]:
        return list(self._defs.values())


# Global registry instance modules import and register against.
registry = PermissionRegistry()


def sync_permissions(reg: PermissionRegistry | None = None) -> None:
    from app.extensions import db
    from app.modules.user_management.models import Permission

    reg = reg or registry
    existing = {p.code for p in Permission.query.all()}
    for d in reg.definitions:
        if d.code not in existing:
            db.session.add(Permission(code=d.code, module=d.module,
                                      action=d.action, description=d.description))
    db.session.flush()
```

- [ ] **Step 4: Run registry tests**

Run: `.venv/bin/pytest tests/unit/test_registry.py -v`
Expected: 2 PASSED

- [ ] **Step 5: Implement decorator**

`app/core/security/decorators.py`:
```python
"""@require_permission('code') — 403s unless current_user holds the permission."""
from functools import wraps

from flask import abort
from flask_login import current_user


def require_permission(code: str):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(401)
            if not current_user.has_permission(code):
                abort(403)
            return fn(*args, **kwargs)
        return wrapper
    return decorator
```

(Integration test for the decorator lands with routes in Task 9.)

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "feat: PermissionRegistry with idempotent sync and require_permission decorator"
```

---

### Task 7: Audit trail (AuditLog model + automatic listener)

**Files:**
- Create: `app/core/models/audit_log.py`, `app/core/audit/__init__.py`, `app/core/audit/audit_service.py`
- Modify: `app/__init__.py` (register listener in factory)
- Test: `tests/unit/test_audit.py`

- [ ] **Step 1: Write failing tests**

`tests/unit/test_audit.py`:
```python
from app.core.models.audit_log import AuditLog
from app.modules.user_management.models import Role


def test_insert_writes_audit_row(db):
    db.session.add(Role(name="Auditors"))
    db.session.commit()
    log = AuditLog.query.filter_by(table_name="roles", action="CREATE").first()
    assert log is not None
    assert log.new_values["name"] == "Auditors"


def test_update_writes_old_and_new(db):
    r = Role(name="Before")
    db.session.add(r)
    db.session.commit()
    r.name = "After"
    db.session.commit()
    log = AuditLog.query.filter_by(table_name="roles", action="UPDATE").first()
    assert log.old_values["name"] == "Before"
    assert log.new_values["name"] == "After"


def test_audit_log_itself_not_audited(db):
    db.session.add(Role(name="R"))
    db.session.commit()
    assert AuditLog.query.filter_by(table_name="audit_logs").count() == 0
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/unit/test_audit.py -v`
Expected: ERROR `ModuleNotFoundError ... audit_log`

- [ ] **Step 3: Implement AuditLog model**

`app/core/models/audit_log.py`:
```python
"""AuditLog: one row per insert/update/delete on any audited model."""
from datetime import datetime, timezone

from app.extensions import db


class AuditLog(db.Model):
    __tablename__ = "audit_logs"
    id = db.Column(db.Integer, primary_key=True)
    table_name = db.Column(db.String(100), nullable=False, index=True)
    record_id = db.Column(db.Integer, nullable=True, index=True)
    action = db.Column(db.String(10), nullable=False)  # CREATE/UPDATE/DELETE
    old_values = db.Column(db.JSON, nullable=True)
    new_values = db.Column(db.JSON, nullable=True)
    user_id = db.Column(db.Integer, nullable=True)
    timestamp = db.Column(db.DateTime,
                          default=lambda: datetime.now(timezone.utc),
                          nullable=False)
    ip_address = db.Column(db.String(45), nullable=True)
```

- [ ] **Step 4: Implement audit listener**

`app/core/audit/__init__.py`: docstring only.

`app/core/audit/audit_service.py`:
```python
"""Automatic audit trail via SQLAlchemy session events.

register_audit_listeners() hooks before_flush and after_flush; every
insert/update/delete on models inheriting BaseModel is logged without any
per-module code. Values are serialised to JSON-safe primitives.
"""
from datetime import datetime, date

from sqlalchemy import event, inspect

from app.extensions import db
from app.core.models.audit_log import AuditLog

_EXCLUDED_TABLES = {"audit_logs"}
_registered = False


def _current_user_id():
    try:
        from flask_login import current_user
        if current_user and current_user.is_authenticated:
            return current_user.id
    except Exception:
        pass
    return None


def _current_ip():
    try:
        from flask import request, has_request_context
        if has_request_context():
            return request.remote_addr
    except Exception:
        pass
    return None


def _serialise(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _row_values(obj):
    mapper = inspect(obj).mapper
    return {c.key: _serialise(getattr(obj, c.key))
            for c in mapper.column_attrs}


def _changed_values(obj):
    state = inspect(obj)
    old, new = {}, {}
    for attr in state.mapper.column_attrs:
        hist = state.attrs[attr.key].history
        if hist.has_changes():
            old[attr.key] = _serialise(hist.deleted[0]) if hist.deleted else None
            new[attr.key] = _serialise(hist.added[0]) if hist.added else None
    return old, new


def register_audit_listeners():
    global _registered
    if _registered:
        return
    _registered = True

    @event.listens_for(db.session.__class__, "before_flush")
    def _before_flush(session, flush_context, instances):
        entries = []
        uid, ip = _current_user_id(), _current_ip()
        for obj in session.new:
            if obj.__tablename__ in _EXCLUDED_TABLES or isinstance(obj, AuditLog):
                continue
            entries.append(AuditLog(table_name=obj.__tablename__, action="CREATE",
                                    new_values=_row_values(obj),
                                    user_id=uid, ip_address=ip))
            session.info.setdefault("_audit_pending_new", []).append((entries[-1], obj))
        for obj in session.dirty:
            if obj.__tablename__ in _EXCLUDED_TABLES or isinstance(obj, AuditLog):
                continue
            if not session.is_modified(obj, include_collections=False):
                continue
            old, new = _changed_values(obj)
            entries.append(AuditLog(table_name=obj.__tablename__, action="UPDATE",
                                    record_id=obj.id, old_values=old,
                                    new_values=new, user_id=uid, ip_address=ip))
        for obj in session.deleted:
            if obj.__tablename__ in _EXCLUDED_TABLES or isinstance(obj, AuditLog):
                continue
            entries.append(AuditLog(table_name=obj.__tablename__, action="DELETE",
                                    record_id=obj.id, old_values=_row_values(obj),
                                    user_id=uid, ip_address=ip))
        session.add_all(entries)

    @event.listens_for(db.session.__class__, "after_flush")
    def _after_flush(session, flush_context):
        # Backfill record_id for CREATE logs now that ids are assigned.
        for log, obj in session.info.pop("_audit_pending_new", []):
            log.record_id = obj.id
```

- [ ] **Step 5: Register in app factory**

In `app/__init__.py`, inside `create_app()` after `csrf.init_app(app)` add:
```python
    from app.core.audit.audit_service import register_audit_listeners
    register_audit_listeners()
```

- [ ] **Step 6: Run tests**

Run: `.venv/bin/pytest tests/unit/test_audit.py -v`
Expected: 3 PASSED
Then run the whole suite: `.venv/bin/pytest tests -v` — all PASSED (earlier tests now also generate audit rows; they don't assert against them, so no breakage expected — if a test fails on flush recursion, verify AuditLog exclusion above).

- [ ] **Step 7: Commit**

```bash
git add -A && git commit -m "feat: automatic audit trail via session flush listeners"
```

---

### Task 8: Auth module (service, forms, routes) + login lockout

**Files:**
- Create: `app/modules/auth/__init__.py`, `app/modules/auth/service.py`, `app/modules/auth/forms.py`, `app/modules/auth/routes.py`
- Test: `tests/unit/test_auth_service.py` (route/template integration lands in Task 10 with the UI shell)

- [ ] **Step 1: Write failing service tests**

`tests/unit/test_auth_service.py`:
```python
import pytest

from app.core.security.password import hash_password
from app.extensions import db as _db
from app.modules.auth.service import AuthService, AccountLockedError
from app.modules.user_management.models import User


@pytest.fixture()
def user(db):
    u = User(username="carol", email="carol@example.com",
             password_hash=hash_password("pw123"))
    db.session.add(u)
    db.session.commit()
    return u


def test_authenticate_success_resets_counter_and_sets_last_login(app, user, db):
    user.failed_login_attempts = 2
    db.session.commit()
    svc = AuthService()
    result = svc.authenticate("carol", "pw123")
    assert result.id == user.id
    assert user.failed_login_attempts == 0
    assert user.last_login_at is not None


def test_authenticate_wrong_password_increments_counter(app, user, db):
    svc = AuthService()
    assert svc.authenticate("carol", "nope") is None
    assert user.failed_login_attempts == 1


def test_lockout_after_max_attempts(app, user, db):
    svc = AuthService()
    for _ in range(app.config["MAX_FAILED_LOGIN_ATTEMPTS"]):
        svc.authenticate("carol", "nope")
    with pytest.raises(AccountLockedError):
        svc.authenticate("carol", "pw123")


def test_authenticate_unknown_user_returns_none(app, db):
    assert AuthService().authenticate("ghost", "pw") is None
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/unit/test_auth_service.py -v`
Expected: ERROR `ModuleNotFoundError: app.modules.auth`

- [ ] **Step 3: Implement AuthService**

`app/modules/auth/__init__.py`: docstring only.

`app/modules/auth/service.py`:
```python
"""AuthService: credential verification, failed-attempt lockout, last-login."""
from datetime import datetime, timezone

from flask import current_app

from app.extensions import db
from app.core.security.password import verify_password
from app.modules.user_management.models import User


class AccountLockedError(Exception):
    """Raised when a locked account attempts to authenticate."""


class AuthService:
    def authenticate(self, username: str, password: str) -> User | None:
        user = User.query.filter_by(username=username, is_active=True).first()
        if user is None:
            return None
        max_attempts = current_app.config["MAX_FAILED_LOGIN_ATTEMPTS"]
        if user.failed_login_attempts >= max_attempts:
            raise AccountLockedError(
                "Account locked after too many failed attempts. "
                "Contact an administrator.")
        if not verify_password(user.password_hash, password):
            user.failed_login_attempts += 1
            db.session.commit()
            return None
        user.failed_login_attempts = 0
        user.last_login_at = datetime.now(timezone.utc)
        db.session.commit()
        return user
```

- [ ] **Step 4: Run service tests**

Run: `.venv/bin/pytest tests/unit/test_auth_service.py -v`
Expected: 4 PASSED

- [ ] **Step 5: Implement forms and routes**

`app/modules/auth/forms.py`:
```python
"""Auth WTForms."""
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, BooleanField
from wtforms.validators import DataRequired, Length, EqualTo


class LoginForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired(), Length(max=80)])
    password = PasswordField("Password", validators=[DataRequired()])
    remember_me = BooleanField("Remember me")


class ChangePasswordForm(FlaskForm):
    current_password = PasswordField("Current password", validators=[DataRequired()])
    new_password = PasswordField("New password",
                                 validators=[DataRequired(), Length(min=8)])
    confirm_password = PasswordField(
        "Confirm new password",
        validators=[DataRequired(), EqualTo("new_password",
                                            message="Passwords must match")])
```

`app/modules/auth/routes.py`:
```python
"""Auth blueprint: login, logout, change password. No business logic here —
all decisions live in AuthService (spec: no logic in controllers)."""
from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_user, logout_user, login_required, current_user

from app.extensions import db
from app.core.security.password import verify_password, hash_password
from app.modules.auth.forms import LoginForm, ChangePasswordForm
from app.modules.auth.service import AuthService, AccountLockedError

bp = Blueprint("auth", __name__, template_folder="templates")


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    form = LoginForm()
    if form.validate_on_submit():
        try:
            user = AuthService().authenticate(form.username.data, form.password.data)
        except AccountLockedError as exc:
            flash(str(exc), "danger")
            return render_template("auth/login.html", form=form)
        if user is None:
            flash("Invalid username or password.", "danger")
            return render_template("auth/login.html", form=form)
        login_user(user, remember=form.remember_me.data)
        if user.must_change_password:
            return redirect(url_for("auth.change_password"))
        next_url = request.args.get("next")
        return redirect(next_url or url_for("main.dashboard"))
    return render_template("auth/login.html", form=form)


@bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))


@bp.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    form = ChangePasswordForm()
    if form.validate_on_submit():
        if not verify_password(current_user.password_hash,
                               form.current_password.data):
            flash("Current password is incorrect.", "danger")
        else:
            current_user.password_hash = hash_password(form.new_password.data)
            current_user.must_change_password = False
            db.session.commit()
            flash("Password updated.", "success")
            return redirect(url_for("main.dashboard"))
    return render_template("auth/change_password.html", form=form)
```

(Blueprint registration + templates land in Task 10 with the UI shell; route tests are integration tests there.)

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "feat: auth service with lockout, login/logout/change-password routes"
```

---

### Task 9: User Management module (repositories, services, schemas, forms, routes)

**Files:**
- Create: `app/modules/user_management/repository.py`, `service.py`, `schemas.py`, `forms.py`, `routes.py`
- Test: `tests/unit/test_user_service.py`

Permission codes this module registers: `user.view`, `user.create`, `user.update`, `user.delete`, `role.view`, `role.create`, `role.update`, `role.delete`, `permission.view`.

- [ ] **Step 1: Write failing service tests**

`tests/unit/test_user_service.py`:
```python
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
    assert User.query.get(u.id).is_active is False


def test_system_role_cannot_be_deleted(db):
    role = Role(name="SuperAdmin", is_system_role=True)
    db.session.add(role)
    db.session.commit()
    with pytest.raises(SystemRoleError):
        RoleService().delete_role(role.id)
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/unit/test_user_service.py -v`
Expected: ERROR `ModuleNotFoundError ... service`

- [ ] **Step 3: Implement repositories**

`app/modules/user_management/repository.py`:
```python
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
```

- [ ] **Step 4: Implement services**

`app/modules/user_management/service.py`:
```python
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
                    last_name=None, role_ids=None, must_change_password=False):
        if self.users.get_by_username(username) is not None:
            raise DuplicateUsernameError(f"Username '{username}' already exists.")
        user = self.users.create(
            username=username, email=email,
            password_hash=hash_password(password),
            first_name=first_name, last_name=last_name,
            must_change_password=must_change_password)
        self._assign_roles(user, role_ids or [])
        db.session.commit()
        return user

    def update_user(self, user_id, *, email=None, first_name=None,
                    last_name=None, role_ids=None, password=None):
        user = self.users.get_by_id(user_id, include_inactive=True)
        if user is None:
            return None
        if email is not None:
            user.email = email
        if first_name is not None:
            user.first_name = first_name
        if last_name is not None:
            user.last_name = last_name
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
```

- [ ] **Step 5: Run service tests**

Run: `.venv/bin/pytest tests/unit/test_user_service.py -v`
Expected: 4 PASSED

- [ ] **Step 6: Implement schemas**

`app/modules/user_management/schemas.py`:
```python
"""Marshmallow schemas (JSON serialisation for AJAX/DataTables endpoints)."""
from marshmallow import Schema, fields


class PermissionSchema(Schema):
    id = fields.Int()
    code = fields.Str()
    module = fields.Str()
    action = fields.Str()
    description = fields.Str()


class RoleSchema(Schema):
    id = fields.Int()
    name = fields.Str()
    description = fields.Str()
    is_system_role = fields.Bool()
    permissions = fields.List(fields.Nested(PermissionSchema))


class UserSchema(Schema):
    id = fields.Int()
    username = fields.Str()
    email = fields.Str()
    first_name = fields.Str()
    last_name = fields.Str()
    full_name = fields.Str()
    is_active = fields.Bool()
    last_login_at = fields.DateTime(allow_none=True)
    roles = fields.List(fields.Nested(RoleSchema(only=("id", "name"))))
```

- [ ] **Step 7: Implement forms**

`app/modules/user_management/forms.py`:
```python
"""WTForms for user/role CRUD. Role/permission choices are populated in the
route from the DB (Select2 multi-selects)."""
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SelectMultipleField, BooleanField
from wtforms.validators import DataRequired, Email, Length, Optional


class UserForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired(), Length(max=80)])
    email = StringField("Email", validators=[DataRequired(), Email()])
    first_name = StringField("First name", validators=[Optional(), Length(max=80)])
    last_name = StringField("Last name", validators=[Optional(), Length(max=80)])
    password = PasswordField("Password", validators=[Optional(), Length(min=8)])
    roles = SelectMultipleField("Roles", coerce=int)
    must_change_password = BooleanField("Require password change at next login")


class RoleForm(FlaskForm):
    name = StringField("Role name", validators=[DataRequired(), Length(max=80)])
    description = StringField("Description", validators=[Optional(), Length(max=255)])
    permissions = SelectMultipleField("Permissions", coerce=int)
```

- [ ] **Step 8: Implement routes (with permission registration)**

`app/modules/user_management/routes.py`:
```python
"""User/Role/Permission admin blueprint. Thin controllers: parse input,
call service, render. Permission checks via @require_permission."""
from flask import Blueprint, render_template, redirect, url_for, flash, jsonify
from flask_login import login_required

from app.core.security.decorators import require_permission
from app.core.security.registry import registry
from app.modules.user_management.forms import UserForm, RoleForm
from app.modules.user_management.models import User, Role, Permission
from app.modules.user_management.repository import (
    UserRepository, RoleRepository, PermissionRepository)
from app.modules.user_management.schemas import UserSchema, RoleSchema, PermissionSchema
from app.modules.user_management.service import (
    UserService, RoleService, DuplicateUsernameError, SystemRoleError)

bp = Blueprint("user_management", __name__, url_prefix="/admin",
               template_folder="templates")

for _code, _desc in [
    ("user.view", "View users"), ("user.create", "Create users"),
    ("user.update", "Update users"), ("user.delete", "Deactivate users"),
    ("role.view", "View roles"), ("role.create", "Create roles"),
    ("role.update", "Update roles"), ("role.delete", "Delete roles"),
    ("permission.view", "View permissions"),
]:
    _module, _action = _code.split(".")
    registry.register(_code, _module, _action, _desc)


def _populate_user_form(form: UserForm) -> None:
    form.roles.choices = [(r.id, r.name)
                          for r in RoleRepository().list()]


def _populate_role_form(form: RoleForm) -> None:
    form.permissions.choices = [(p.id, p.code)
                                for p in PermissionRepository().list()]


# ---------- Users ----------

@bp.route("/users")
@login_required
@require_permission("user.view")
def users_list():
    users = UserRepository().list(include_inactive=True)
    return render_template("user_management/users_list.html", users=users)


@bp.route("/users/data")
@login_required
@require_permission("user.view")
def users_data():
    users = UserRepository().list(include_inactive=True)
    return jsonify(data=UserSchema(many=True).dump(users))


@bp.route("/users/new", methods=["GET", "POST"])
@login_required
@require_permission("user.create")
def users_new():
    form = UserForm()
    _populate_user_form(form)
    if form.validate_on_submit():
        try:
            UserService().create_user(
                username=form.username.data, email=form.email.data,
                password=form.password.data or "ChangeMe123!",
                first_name=form.first_name.data, last_name=form.last_name.data,
                role_ids=form.roles.data,
                must_change_password=form.must_change_password.data or not form.password.data)
            flash("User created.", "success")
            return redirect(url_for("user_management.users_list"))
        except DuplicateUsernameError as exc:
            flash(str(exc), "danger")
    return render_template("user_management/user_form.html", form=form,
                           title="New User")


@bp.route("/users/<int:user_id>/edit", methods=["GET", "POST"])
@login_required
@require_permission("user.update")
def users_edit(user_id):
    user = UserRepository().get_by_id(user_id, include_inactive=True)
    if user is None:
        flash("User not found.", "warning")
        return redirect(url_for("user_management.users_list"))
    form = UserForm(obj=user)
    _populate_user_form(form)
    if form.validate_on_submit():
        UserService().update_user(
            user_id, email=form.email.data, first_name=form.first_name.data,
            last_name=form.last_name.data, role_ids=form.roles.data,
            password=form.password.data or None)
        flash("User updated.", "success")
        return redirect(url_for("user_management.users_list"))
    form.roles.data = [r.id for r in user.roles]
    return render_template("user_management/user_form.html", form=form,
                           title=f"Edit User — {user.username}")


@bp.route("/users/<int:user_id>/deactivate", methods=["POST"])
@login_required
@require_permission("user.delete")
def users_deactivate(user_id):
    UserService().deactivate_user(user_id)
    flash("User deactivated.", "info")
    return redirect(url_for("user_management.users_list"))


# ---------- Roles ----------

@bp.route("/roles")
@login_required
@require_permission("role.view")
def roles_list():
    roles = RoleRepository().list(include_inactive=True)
    return render_template("user_management/roles_list.html", roles=roles)


@bp.route("/roles/new", methods=["GET", "POST"])
@login_required
@require_permission("role.create")
def roles_new():
    form = RoleForm()
    _populate_role_form(form)
    if form.validate_on_submit():
        RoleService().create_role(name=form.name.data,
                                  description=form.description.data,
                                  permission_ids=form.permissions.data)
        flash("Role created.", "success")
        return redirect(url_for("user_management.roles_list"))
    return render_template("user_management/role_form.html", form=form,
                           title="New Role")


@bp.route("/roles/<int:role_id>/edit", methods=["GET", "POST"])
@login_required
@require_permission("role.update")
def roles_edit(role_id):
    role = RoleRepository().get_by_id(role_id)
    if role is None:
        flash("Role not found.", "warning")
        return redirect(url_for("user_management.roles_list"))
    form = RoleForm(obj=role)
    _populate_role_form(form)
    if form.validate_on_submit():
        RoleService().update_role(role_id, name=form.name.data,
                                  description=form.description.data,
                                  permission_ids=form.permissions.data)
        flash("Role updated.", "success")
        return redirect(url_for("user_management.roles_list"))
    form.permissions.data = [p.id for p in role.permissions]
    return render_template("user_management/role_form.html", form=form,
                           title=f"Edit Role — {role.name}")


@bp.route("/roles/<int:role_id>/delete", methods=["POST"])
@login_required
@require_permission("role.delete")
def roles_delete(role_id):
    try:
        RoleService().delete_role(role_id)
        flash("Role deleted.", "info")
    except SystemRoleError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("user_management.roles_list"))


# ---------- Permissions (read-only; managed by PermissionRegistry) ----------

@bp.route("/permissions")
@login_required
@require_permission("permission.view")
def permissions_list():
    perms = PermissionRepository().list()
    return render_template("user_management/permissions_list.html",
                           permissions=perms)
```

- [ ] **Step 9: Run full suite (routes not yet registered — unit tests only must pass)**

Run: `.venv/bin/pytest tests -v`
Expected: all PASSED

- [ ] **Step 10: Commit**

```bash
git add -A && git commit -m "feat: user management module (repos, services, schemas, forms, routes)"
```

---

### Task 10: UI shell, templates, blueprint registration, error handlers

**Files:**
- Create: `app/templates/layout/base.html`, `app/templates/layout/sidebar.html`, `app/templates/layout/topnav.html`
- Create: `app/templates/errors/403.html`, `404.html`, `500.html`
- Create: `app/modules/auth/templates/auth/login.html`, `change_password.html`
- Create: `app/modules/user_management/templates/user_management/users_list.html`, `user_form.html`, `roles_list.html`, `role_form.html`, `permissions_list.html`
- Create: `app/modules/main/__init__.py`, `app/modules/main/routes.py`, `app/modules/main/templates/main/dashboard.html`
- Create: `app/static/css/app.css`, `app/static/js/app.js`
- Modify: `app/__init__.py` (register blueprints, error handlers, permission sync)
- Test: `tests/integration/test_auth_flow.py`, `tests/integration/test_permission_enforcement.py`

- [ ] **Step 1: Write failing integration tests**

`tests/integration/test_auth_flow.py`:
```python
from app.core.security.password import hash_password
from app.modules.user_management.models import User


def _make_user(db, username="gina", password="pw123456"):
    u = User(username=username, email=f"{username}@example.com",
             password_hash=hash_password(password))
    db.session.add(u)
    db.session.commit()
    return u


def test_login_page_renders(client):
    resp = client.get("/login")
    assert resp.status_code == 200
    assert b"Username" in resp.data


def test_login_success_redirects_to_dashboard(client, db):
    _make_user(db)
    resp = client.post("/login", data={"username": "gina",
                                       "password": "pw123456"},
                       follow_redirects=True)
    assert resp.status_code == 200
    assert b"Dashboard" in resp.data


def test_login_failure_shows_error(client, db):
    _make_user(db)
    resp = client.post("/login", data={"username": "gina", "password": "bad"})
    assert b"Invalid username or password" in resp.data


def test_dashboard_requires_login(client):
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_logout(client, db):
    _make_user(db)
    client.post("/login", data={"username": "gina", "password": "pw123456"})
    resp = client.get("/logout", follow_redirects=True)
    assert b"logged out" in resp.data.lower()
```

`tests/integration/test_permission_enforcement.py`:
```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/integration -v`
Expected: FAIL — 404s (blueprints not registered) / missing templates.

- [ ] **Step 3: Create main (dashboard) module**

`app/modules/main/__init__.py`: docstring only.

`app/modules/main/routes.py`:
```python
"""Landing dashboard. KPI cards are placeholders until Phase 4."""
from flask import Blueprint, render_template
from flask_login import login_required

bp = Blueprint("main", __name__, template_folder="templates")


@bp.route("/")
@login_required
def dashboard():
    placeholder_cards = [
        {"title": "Fleet", "icon": "bi-truck", "value": "—"},
        {"title": "Maintenance", "icon": "bi-wrench", "value": "—"},
        {"title": "Approvals", "icon": "bi-check2-square", "value": "—"},
        {"title": "Registrations", "icon": "bi-card-checklist", "value": "—"},
        {"title": "Tires", "icon": "bi-circle", "value": "—"},
        {"title": "Batteries", "icon": "bi-battery-half", "value": "—"},
    ]
    return render_template("main/dashboard.html", cards=placeholder_cards)
```

- [ ] **Step 4: Create layout templates**

`app/templates/layout/base.html`:
```html
<!doctype html>
<html lang="en" data-bs-theme="light">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{% block title %}FMS{% endblock %} · Fleet Management System</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
  <link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css" rel="stylesheet">
  <link href="https://cdn.datatables.net/1.13.8/css/dataTables.bootstrap5.min.css" rel="stylesheet">
  <link href="https://cdn.jsdelivr.net/npm/select2@4.1.0-rc.0/dist/css/select2.min.css" rel="stylesheet">
  <link href="{{ url_for('static', filename='css/app.css') }}" rel="stylesheet">
</head>
<body>
{% if current_user.is_authenticated %}
<div class="d-flex" id="fms-wrapper">
  {% include "layout/sidebar.html" %}
  <div class="flex-grow-1 d-flex flex-column min-vh-100">
    {% include "layout/topnav.html" %}
    <main class="container-fluid p-4 flex-grow-1">
      <nav aria-label="breadcrumb">
        <ol class="breadcrumb">
          <li class="breadcrumb-item"><a href="{{ url_for('main.dashboard') }}">Home</a></li>
          {% block breadcrumbs %}{% endblock %}
        </ol>
      </nav>
      {% include "layout/_flash.html" %}
      {% block content %}{% endblock %}
    </main>
    <footer class="text-center text-muted small py-2 border-top">
      FMS — Enterprise Fleet Management System
    </footer>
  </div>
</div>
{% else %}
<main class="container-fluid p-0">
  {% include "layout/_flash.html" %}
  {% block auth_content %}{% endblock %}
</main>
{% endif %}
<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
<script src="https://code.jquery.com/jquery-3.7.1.min.js"></script>
<script src="https://cdn.datatables.net/1.13.8/js/jquery.dataTables.min.js"></script>
<script src="https://cdn.datatables.net/1.13.8/js/dataTables.bootstrap5.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/select2@4.1.0-rc.0/dist/js/select2.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/sweetalert2@11"></script>
<script src="{{ url_for('static', filename='js/app.js') }}"></script>
{% block scripts %}{% endblock %}
</body>
</html>
```

`app/templates/layout/_flash.html`:
```html
{% with messages = get_flashed_messages(with_categories=true) %}
  {% for category, message in messages %}
    <div class="alert alert-{{ category }} alert-dismissible fade show" role="alert">
      {{ message }}
      <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
    </div>
  {% endfor %}
{% endwith %}
```

(Also add `_flash.html` to the Files list above — same directory.)

`app/templates/layout/sidebar.html`:
```html
<aside class="fms-sidebar d-flex flex-column p-3" id="fmsSidebar">
  <a href="{{ url_for('main.dashboard') }}" class="d-flex align-items-center mb-3 text-decoration-none fs-5 fw-semibold sidebar-brand">
    <i class="bi bi-truck-front me-2"></i><span class="sidebar-label">FMS</span>
  </a>
  <ul class="nav nav-pills flex-column mb-auto">
    <li class="nav-item">
      <a href="{{ url_for('main.dashboard') }}" class="nav-link {{ 'active' if request.endpoint == 'main.dashboard' }}">
        <i class="bi bi-speedometer2 me-2"></i><span class="sidebar-label">Dashboard</span>
      </a>
    </li>
    <li class="mt-3 mb-1 text-uppercase small sidebar-section sidebar-label">System Administration</li>
    {% if current_user.has_permission('user.view') %}
    <li><a href="{{ url_for('user_management.users_list') }}" class="nav-link {{ 'active' if request.endpoint and request.endpoint.startswith('user_management.users') }}">
      <i class="bi bi-people me-2"></i><span class="sidebar-label">Users</span></a></li>
    {% endif %}
    {% if current_user.has_permission('role.view') %}
    <li><a href="{{ url_for('user_management.roles_list') }}" class="nav-link {{ 'active' if request.endpoint and request.endpoint.startswith('user_management.roles') }}">
      <i class="bi bi-person-badge me-2"></i><span class="sidebar-label">Roles</span></a></li>
    {% endif %}
    {% if current_user.has_permission('permission.view') %}
    <li><a href="{{ url_for('user_management.permissions_list') }}" class="nav-link {{ 'active' if request.endpoint == 'user_management.permissions_list' }}">
      <i class="bi bi-shield-lock me-2"></i><span class="sidebar-label">Permissions</span></a></li>
    {% endif %}
  </ul>
</aside>
```

`app/templates/layout/topnav.html`:
```html
<nav class="navbar border-bottom px-3 fms-topnav">
  <button class="btn btn-outline-secondary btn-sm" id="sidebarToggle" type="button" title="Toggle sidebar">
    <i class="bi bi-list"></i>
  </button>
  <div class="d-flex align-items-center gap-3 ms-auto">
    <button class="btn btn-outline-secondary btn-sm" id="darkModeToggle" type="button" title="Toggle dark mode">
      <i class="bi bi-moon"></i>
    </button>
    <button class="btn btn-outline-secondary btn-sm position-relative" type="button" title="Notifications (coming soon)" disabled>
      <i class="bi bi-bell"></i>
    </button>
    <div class="dropdown">
      <a class="d-flex align-items-center text-decoration-none dropdown-toggle" data-bs-toggle="dropdown" href="#">
        <i class="bi bi-person-circle me-2 fs-5"></i>{{ current_user.full_name }}
      </a>
      <ul class="dropdown-menu dropdown-menu-end">
        <li><a class="dropdown-item" href="{{ url_for('auth.change_password') }}">Change password</a></li>
        <li><hr class="dropdown-divider"></li>
        <li><a class="dropdown-item" href="{{ url_for('auth.logout') }}">Sign out</a></li>
      </ul>
    </div>
  </div>
</nav>
```

- [ ] **Step 5: Create static assets**

`app/static/css/app.css`:
```css
/* FMS shell styling: sidebar, dark mode, dashboard cards */
.fms-sidebar {
  width: 240px;
  min-height: 100vh;
  background: var(--bs-tertiary-bg);
  border-right: 1px solid var(--bs-border-color);
  transition: width .15s ease;
}
#fms-wrapper.sidebar-collapsed .fms-sidebar { width: 64px; }
#fms-wrapper.sidebar-collapsed .sidebar-label { display: none; }
.fms-sidebar .nav-link { color: var(--bs-body-color); }
.fms-sidebar .nav-link.active { background: var(--bs-primary); color: #fff; }
.sidebar-section { color: var(--bs-secondary-color); letter-spacing: .05em; }
.fms-topnav { background: var(--bs-body-bg); }
.kpi-card .bi { font-size: 1.8rem; opacity: .6; }
```

`app/static/js/app.js`:
```javascript
/* Shell behaviour: dark-mode + sidebar-collapse persisted via cookie,
   DataTables/Select2 auto-init. */
(function () {
  function setCookie(name, value) {
    document.cookie = name + "=" + value + ";path=/;max-age=31536000;SameSite=Lax";
  }
  function getCookie(name) {
    const m = document.cookie.match("(^|;)\\s*" + name + "\\s*=\\s*([^;]+)");
    return m ? m.pop() : "";
  }

  // Dark mode
  const savedTheme = getCookie("fms-theme");
  if (savedTheme) document.documentElement.setAttribute("data-bs-theme", savedTheme);
  const darkToggle = document.getElementById("darkModeToggle");
  if (darkToggle) {
    darkToggle.addEventListener("click", function () {
      const html = document.documentElement;
      const next = html.getAttribute("data-bs-theme") === "dark" ? "light" : "dark";
      html.setAttribute("data-bs-theme", next);
      setCookie("fms-theme", next);
    });
  }

  // Sidebar collapse
  const wrapper = document.getElementById("fms-wrapper");
  if (wrapper && getCookie("fms-sidebar") === "collapsed") {
    wrapper.classList.add("sidebar-collapsed");
  }
  const sbToggle = document.getElementById("sidebarToggle");
  if (sbToggle) {
    sbToggle.addEventListener("click", function () {
      wrapper.classList.toggle("sidebar-collapsed");
      setCookie("fms-sidebar",
        wrapper.classList.contains("sidebar-collapsed") ? "collapsed" : "open");
    });
  }

  // Auto-init DataTables and Select2 when jQuery is present
  if (window.jQuery) {
    jQuery(function ($) {
      $("table.fms-datatable").DataTable({ pageLength: 25 });
      $("select.fms-select2").select2({ width: "100%", theme: "default" });
    });
  }
})();
```

- [ ] **Step 6: Create error templates**

`app/templates/errors/403.html`:
```html
{% extends "layout/base.html" %}
{% block title %}403{% endblock %}
{% set body %}
<div class="text-center py-5">
  <h1 class="display-4">403</h1>
  <p class="lead">You don't have permission to access this page.</p>
  <a class="btn btn-primary" href="{{ url_for('main.dashboard') }}">Back to Dashboard</a>
</div>
{% endset %}
{% block content %}{{ body }}{% endblock %}
{% block auth_content %}{{ body }}{% endblock %}
```

`404.html` and `500.html`: identical structure with code/message swapped
(404 — "Page not found."; 500 — "Something went wrong on our side.").

- [ ] **Step 7: Create auth templates**

`app/modules/auth/templates/auth/login.html`:
```html
{% extends "layout/base.html" %}
{% block title %}Sign in{% endblock %}
{% block auth_content %}
<div class="d-flex align-items-center justify-content-center min-vh-100 bg-body-tertiary">
  <div class="card shadow-sm" style="width: 380px;">
    <div class="card-body p-4">
      <div class="text-center mb-4">
        <i class="bi bi-truck-front fs-1 text-primary"></i>
        <h1 class="h4 mt-2">Fleet Management System</h1>
        <p class="text-muted small">Sign in to continue</p>
      </div>
      <form method="post" novalidate>
        {{ form.hidden_tag() }}
        <div class="mb-3">
          {{ form.username.label(class="form-label") }}
          {{ form.username(class="form-control", autofocus=True) }}
        </div>
        <div class="mb-3">
          {{ form.password.label(class="form-label") }}
          {{ form.password(class="form-control") }}
        </div>
        <div class="mb-3 form-check">
          {{ form.remember_me(class="form-check-input") }}
          {{ form.remember_me.label(class="form-check-label") }}
        </div>
        <button class="btn btn-primary w-100" type="submit">Sign in</button>
      </form>
    </div>
  </div>
</div>
{% endblock %}
```

`app/modules/auth/templates/auth/change_password.html`:
```html
{% extends "layout/base.html" %}
{% block title %}Change password{% endblock %}
{% block breadcrumbs %}<li class="breadcrumb-item active">Change Password</li>{% endblock %}
{% set form_body %}
<div class="row justify-content-center"><div class="col-md-5">
  <div class="card"><div class="card-body">
    <h2 class="h5 mb-3">Change Password</h2>
    <form method="post" novalidate>
      {{ form.hidden_tag() }}
      {% for field in [form.current_password, form.new_password, form.confirm_password] %}
      <div class="mb-3">
        {{ field.label(class="form-label") }}
        {{ field(class="form-control") }}
        {% for err in field.errors %}<div class="text-danger small">{{ err }}</div>{% endfor %}
      </div>
      {% endfor %}
      <button class="btn btn-primary" type="submit">Update password</button>
    </form>
  </div></div>
</div></div>
{% endset %}
{% block content %}{{ form_body }}{% endblock %}
{% block auth_content %}{{ form_body }}{% endblock %}
```

- [ ] **Step 8: Create dashboard + user management templates**

`app/modules/main/templates/main/dashboard.html`:
```html
{% extends "layout/base.html" %}
{% block title %}Dashboard{% endblock %}
{% block breadcrumbs %}<li class="breadcrumb-item active">Dashboard</li>{% endblock %}
{% block content %}
<h1 class="h3 mb-4">Dashboard</h1>
<div class="row g-3">
  {% for card in cards %}
  <div class="col-6 col-md-4 col-xl-2">
    <div class="card kpi-card h-100">
      <div class="card-body d-flex justify-content-between align-items-center">
        <div>
          <div class="text-muted small">{{ card.title }}</div>
          <div class="fs-4 fw-semibold">{{ card.value }}</div>
        </div>
        <i class="bi {{ card.icon }}"></i>
      </div>
    </div>
  </div>
  {% endfor %}
</div>
<p class="text-muted mt-4 small">KPI data will be populated in Phase 4 (Dashboard).</p>
{% endblock %}
```

`app/modules/user_management/templates/user_management/users_list.html`:
```html
{% extends "layout/base.html" %}
{% block title %}Users{% endblock %}
{% block breadcrumbs %}<li class="breadcrumb-item active">Users</li>{% endblock %}
{% block content %}
<div class="d-flex justify-content-between align-items-center mb-3">
  <h1 class="h3 mb-0">Users</h1>
  {% if current_user.has_permission('user.create') %}
  <a class="btn btn-primary" href="{{ url_for('user_management.users_new') }}">
    <i class="bi bi-plus-lg me-1"></i>New User</a>
  {% endif %}
</div>
<div class="card"><div class="card-body">
<table class="table table-striped fms-datatable">
  <thead><tr>
    <th>Username</th><th>Name</th><th>Email</th><th>Roles</th>
    <th>Active</th><th>Last login</th><th></th>
  </tr></thead>
  <tbody>
  {% for u in users %}
    <tr>
      <td>{{ u.username }}</td>
      <td>{{ u.full_name }}</td>
      <td>{{ u.email }}</td>
      <td>{% for r in u.roles %}<span class="badge text-bg-secondary">{{ r.name }}</span> {% endfor %}</td>
      <td>{% if u.is_active %}<span class="badge text-bg-success">Yes</span>
          {% else %}<span class="badge text-bg-danger">No</span>{% endif %}</td>
      <td>{{ u.last_login_at or '—' }}</td>
      <td class="text-end">
        {% if current_user.has_permission('user.update') %}
        <a class="btn btn-sm btn-outline-secondary"
           href="{{ url_for('user_management.users_edit', user_id=u.id) }}">
          <i class="bi bi-pencil"></i></a>
        {% endif %}
        {% if current_user.has_permission('user.delete') and u.is_active %}
        <form method="post" class="d-inline fms-confirm"
              data-confirm="Deactivate {{ u.username }}?"
              action="{{ url_for('user_management.users_deactivate', user_id=u.id) }}">
          <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
          <button class="btn btn-sm btn-outline-danger"><i class="bi bi-person-x"></i></button>
        </form>
        {% endif %}
      </td>
    </tr>
  {% endfor %}
  </tbody>
</table>
</div></div>
{% endblock %}
{% block scripts %}
<script>
document.querySelectorAll("form.fms-confirm").forEach(function (f) {
  f.addEventListener("submit", function (e) {
    e.preventDefault();
    Swal.fire({title: f.dataset.confirm, icon: "warning",
               showCancelButton: true, confirmButtonText: "Yes"})
      .then(function (r) { if (r.isConfirmed) f.submit(); });
  });
});
</script>
{% endblock %}
```

`app/modules/user_management/templates/user_management/user_form.html`:
```html
{% extends "layout/base.html" %}
{% block title %}{{ title }}{% endblock %}
{% block breadcrumbs %}
<li class="breadcrumb-item"><a href="{{ url_for('user_management.users_list') }}">Users</a></li>
<li class="breadcrumb-item active">{{ title }}</li>
{% endblock %}
{% block content %}
<h1 class="h3 mb-4">{{ title }}</h1>
<div class="card"><div class="card-body">
<form method="post" novalidate class="row g-3">
  {{ form.hidden_tag() }}
  <div class="col-md-6">{{ form.username.label(class="form-label") }}{{ form.username(class="form-control") }}
    {% for e in form.username.errors %}<div class="text-danger small">{{ e }}</div>{% endfor %}</div>
  <div class="col-md-6">{{ form.email.label(class="form-label") }}{{ form.email(class="form-control") }}
    {% for e in form.email.errors %}<div class="text-danger small">{{ e }}</div>{% endfor %}</div>
  <div class="col-md-6">{{ form.first_name.label(class="form-label") }}{{ form.first_name(class="form-control") }}</div>
  <div class="col-md-6">{{ form.last_name.label(class="form-label") }}{{ form.last_name(class="form-control") }}</div>
  <div class="col-md-6">{{ form.password.label(class="form-label") }}{{ form.password(class="form-control") }}
    <div class="form-text">Leave blank to keep unchanged (edit) or auto-assign a temp password (new).</div>
    {% for e in form.password.errors %}<div class="text-danger small">{{ e }}</div>{% endfor %}</div>
  <div class="col-md-6">{{ form.roles.label(class="form-label") }}{{ form.roles(class="form-select fms-select2", multiple=True) }}</div>
  <div class="col-12 form-check ms-2">{{ form.must_change_password(class="form-check-input") }}{{ form.must_change_password.label(class="form-check-label") }}</div>
  <div class="col-12">
    <button class="btn btn-primary" type="submit">Save</button>
    <a class="btn btn-outline-secondary" href="{{ url_for('user_management.users_list') }}">Cancel</a>
  </div>
</form>
</div></div>
{% endblock %}
```

`roles_list.html`, `role_form.html`, `permissions_list.html`: same patterns as the two above —
roles table (Name / Description / System / Permissions count / actions with SweetAlert delete),
role form (name, description, permissions multi-select with `fms-select2`), permissions read-only
table (Code / Module / Action / Description) with `fms-datatable`.

- [ ] **Step 9: Register everything in the app factory**

In `app/__init__.py`, replace the comment placeholder in `create_app()` with:
```python
    from app.modules.auth.routes import bp as auth_bp
    from app.modules.main.routes import bp as main_bp
    from app.modules.user_management.routes import bp as user_mgmt_bp
    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(user_mgmt_bp)

    from flask import render_template

    @app.errorhandler(403)
    def forbidden(_e):
        return render_template("errors/403.html"), 403

    @app.errorhandler(404)
    def not_found(_e):
        return render_template("errors/404.html"), 404

    @app.errorhandler(500)
    def server_error(_e):
        return render_template("errors/500.html"), 500
```

Note: permission sync at startup requires tables to exist, so it is exposed
via CLI (`flask seed permissions`, Task 11) and called in tests explicitly,
rather than run unconditionally in the factory (which would crash on a
fresh DB before migrations).

- [ ] **Step 10: Run the full suite**

Run: `.venv/bin/pytest tests -v`
Expected: all PASSED (including the 8 new integration tests)

- [ ] **Step 11: Manual smoke test**

Run: `cd /home/claude/fms && FLASK_ENV=development .venv/bin/flask --app wsgi shell -c "from app.extensions import db; db.create_all()" 2>/dev/null || .venv/bin/python -c "
from app import create_app
from app.extensions import db
app = create_app('development')
with app.app_context():
    db.create_all()
print('DB created')"`
Expected: `DB created`

- [ ] **Step 12: Commit**

```bash
git add -A && git commit -m "feat: enterprise UI shell, templates, blueprint registration, error handlers"
```

---

### Task 11: Seed CLI (admin user, system role, permission sync)

**Files:**
- Create: `app/cli.py`
- Modify: `app/__init__.py` (register CLI group)
- Test: `tests/integration/test_cli_seed.py`

- [ ] **Step 1: Write failing test**

`tests/integration/test_cli_seed.py`:
```python
from app.modules.user_management.models import User, Role, Permission


def test_seed_all_creates_admin_with_all_permissions(app, db):
    runner = app.test_cli_runner()
    result = runner.invoke(args=["seed", "all", "--admin-password", "Admin123!"])
    assert result.exit_code == 0, result.output
    admin = User.query.filter_by(username="admin").first()
    assert admin is not None
    role = Role.query.filter_by(name="System Administrator").first()
    assert role.is_system_role is True
    assert Permission.query.count() > 0
    assert admin.has_permission("user.create")


def test_seed_all_is_idempotent(app, db):
    runner = app.test_cli_runner()
    runner.invoke(args=["seed", "all", "--admin-password", "Admin123!"])
    result = runner.invoke(args=["seed", "all", "--admin-password", "Admin123!"])
    assert result.exit_code == 0
    assert User.query.filter_by(username="admin").count() == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/integration/test_cli_seed.py -v`
Expected: FAIL — no such command "seed"

- [ ] **Step 3: Implement CLI**

`app/cli.py`:
```python
"""Seed commands: `flask seed permissions|admin|all`."""
import click
from flask.cli import AppGroup

from app.extensions import db
from app.core.security.password import hash_password
from app.core.security.registry import sync_permissions
from app.modules.user_management.models import User, Role, Permission

seed_cli = AppGroup("seed", help="Seed initial data.")


@seed_cli.command("permissions")
def seed_permissions():
    """Sync code-registered permissions into the database."""
    sync_permissions()
    db.session.commit()
    click.echo(f"Permissions synced: {Permission.query.count()} total.")


@seed_cli.command("admin")
@click.option("--admin-password", prompt=True, hide_input=True,
              help="Password for the admin account.")
def seed_admin(admin_password):
    """Create the System Administrator role (all permissions) and admin user."""
    _seed_admin(admin_password)


@seed_cli.command("all")
@click.option("--admin-password", prompt=True, hide_input=True)
def seed_all(admin_password):
    """Sync permissions then create the admin role/user."""
    sync_permissions()
    db.session.commit()
    _seed_admin(admin_password)


def _seed_admin(admin_password: str) -> None:
    role = Role.query.filter_by(name="System Administrator").first()
    if role is None:
        role = Role(name="System Administrator",
                    description="Full access to all modules",
                    is_system_role=True)
        db.session.add(role)
    role.permissions = Permission.query.all()

    admin = User.query.filter_by(username="admin").first()
    if admin is None:
        admin = User(username="admin", email="admin@example.com",
                     password_hash=hash_password(admin_password),
                     first_name="System", last_name="Administrator",
                     must_change_password=True)
        admin.roles.append(role)
        db.session.add(admin)
        click.echo("Admin user created (username: admin).")
    else:
        click.echo("Admin user already exists; skipped.")
    db.session.commit()


def register_cli(app):
    app.cli.add_command(seed_cli)
```

In `app/__init__.py` `create_app()`, after blueprint registration add:
```python
    from app.cli import register_cli
    register_cli(app)
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/integration/test_cli_seed.py -v`
Expected: 2 PASSED. Then full suite: `.venv/bin/pytest tests -v` — all PASSED.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: seed CLI for permissions, system role and admin user"
```

---

### Task 12: Migrations, README, packaging

**Files:**
- Create: `migrations/` (generated), `README.md`
- Modify: none

- [ ] **Step 1: Initialise migrations and generate the initial revision**

Run:
```bash
cd /home/claude/fms && \
FLASK_APP=wsgi .venv/bin/flask db init && \
FLASK_APP=wsgi .venv/bin/flask db migrate -m "phase 1a: users, roles, permissions, audit log" && \
FLASK_APP=wsgi .venv/bin/flask db upgrade
```
Expected: `migrations/versions/<hash>_phase_1a_...py` created; upgrade runs clean; `instance/fms_dev.db` exists.

- [ ] **Step 2: Write README.md**

```markdown
# FMS — Enterprise Fleet Management System

Phase 1a foundation: Flask app factory, RBAC (users/roles/permissions),
session auth with lockout, automatic audit trail, Enterprise UI shell.

## Quick start (PyCharm / local)

1. Open the `fms/` folder as a PyCharm project.
2. Create a virtualenv (Python 3.11+; 3.13 recommended) and install deps:
   `pip install -r requirements.txt`
3. Copy `.env.example` to `.env` and adjust if needed.
4. Initialise the database:
   `flask --app wsgi db upgrade`
5. Seed permissions + admin:
   `flask --app wsgi seed all`  (choose an admin password when prompted)
6. Run: `flask --app wsgi run` and open http://127.0.0.1:5000
   Log in as `admin` / your password (you'll be asked to change it).

Celery worker (optional in 1a; used by the Notification Engine later):
`celery -A celery_worker.celery worker --loglevel=info` (requires Redis).

## Tests
`pytest tests -v`

## Architecture
- `app/core/` — cross-cutting: BaseModel, BaseRepository, audit trail,
  security (Argon2, RBAC decorator, PermissionRegistry), Celery factory.
- `app/modules/<feature>/` — feature-first packages: models, repository,
  service, routes, forms, schemas, templates.
- Controllers contain no business logic; services own rules; repositories
  own persistence.

See `docs/superpowers/specs/` for design specs and `docs/superpowers/plans/`
for implementation plans.
```

- [ ] **Step 3: Run the entire suite one final time**

Run: `.venv/bin/pytest tests -v`
Expected: all PASSED

- [ ] **Step 4: Commit**

```bash
git add -A && git commit -m "chore: initial migration, README, phase 1a complete"
```

- [ ] **Step 5: Package for download**

```bash
cd /home/claude && zip -rq fms-phase1a.zip fms -x "fms/.venv/*" "fms/instance/*" "fms/__pycache__/*" "fms/**/__pycache__/*"
cp fms-phase1a.zip /mnt/user-data/outputs/
```
Expected: zip in outputs, presented to the user.

---

## Self-Review Notes

- **Spec coverage:** folder structure (T1), BaseModel (T2), BaseRepository (T3), User/Role/Permission + M2M (T4), Argon2 (T5), PermissionRegistry + decorator (T6), AuditLog + automatic listener (T7), auth + lockout + session timeout config (T1/T8), UI shell with sidebar/topnav/breadcrumbs/dark mode/login (T10), error handlers + JSON logging (T1/T10), seed CLI (T11), migrations/README/zip (T12), tests throughout. CSRF via Flask-WTF enabled in config (T1) and used in forms/templates.
- **Deferred per spec:** JWT auth, System Parameters, engines (1b), notification content (bell is a disabled placeholder).
- **Type consistency check:** `has_permission` defined T4, used T6/T10 templates; `require_permission` defined T6, used T9; `AccountLockedError` defined and consumed T8; repository method names uniform.
- **Known judgment calls:** permission sync via CLI not factory startup (fresh-DB safety); `created_by/updated_by` populated later when a request-context hook is added (kept nullable); Branch FK deferred to Phase 2.
