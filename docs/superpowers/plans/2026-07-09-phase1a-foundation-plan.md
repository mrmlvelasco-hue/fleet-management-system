# Phase 1a — Foundation: Architecture, Auth & RBAC — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the FMS project skeleton: app factory, config, base model/repository infra, Audit Trail engine, Auth (Flask-Login), RBAC (User/Role/Permission), Enterprise UI shell, and Celery/Redis wiring — a working, testable Flask app with login and permission-gated CRUD screens for Users/Roles/Permissions.

**Architecture:** Feature-first modules (`app/modules/<feature>/`) each with models/repository/service/routes/forms/schemas, backed by a `app/core/` package holding cross-cutting engines (BaseModel, BaseRepository, AuditService, security/RBAC). SQLite for dev/test via SQLAlchemy, MySQL-compatible types throughout.

**Tech Stack:** Python 3.13, Flask, Flask-SQLAlchemy, Flask-Migrate, Flask-Login, Flask-WTF, Marshmallow, Celery, Redis, Argon2 (via `argon2-cffi`), Bootstrap 5, Jinja2, DataTables, SweetAlert2, Select2, pytest.

**Reference spec:** `docs/superpowers/specs/2026-07-09-phase1a-foundation-design.md`

---

## Task 0: Project Scaffolding

**Files:**
- Create: `requirements.txt`
- Create: `.env.example`
- Create: `.gitignore`
- Create: `instance/.gitkeep`

- [ ] **Step 1: Create `requirements.txt`**

```
Flask==3.0.3
Flask-SQLAlchemy==3.1.1
Flask-Migrate==4.0.7
Flask-Login==0.6.3
Flask-WTF==1.2.1
marshmallow==3.21.3
celery==5.4.0
redis==5.0.7
argon2-cffi==23.1.0
python-dotenv==1.0.1
pytest==8.2.2
pytest-flask==1.3.0
```

- [ ] **Step 2: Create `.env.example`**

```
FLASK_ENV=development
SECRET_KEY=change-me-in-production
DATABASE_URL=sqlite:///instance/fms_dev.sqlite
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/1
SESSION_TIMEOUT_MINUTES=30
ACCOUNT_LOCKOUT_ATTEMPTS=5
```

- [ ] **Step 3: Create `.gitignore`**

```
__pycache__/
*.pyc
.venv/
venv/
instance/*.sqlite
.env
*.egg-info/
.pytest_cache/
```

- [ ] **Step 4: Create `instance/.gitkeep` (empty file) and commit**

```bash
mkdir -p instance
touch instance/.gitkeep
git add requirements.txt .env.example .gitignore instance/.gitkeep
git commit -m "chore: project scaffolding (requirements, env, gitignore)"
```

---

## Task 1: Config & App Factory

**Files:**
- Create: `app/__init__.py`
- Create: `app/config.py`
- Create: `app/extensions.py`
- Create: `wsgi.py`
- Test: `tests/unit/test_app_factory.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_app_factory.py
import pytest
from app import create_app


@pytest.fixture
def app():
    app = create_app("testing")
    yield app


@pytest.fixture
def client(app):
    return app.test_client()


def test_app_is_created_with_testing_config(app):
    assert app.config["TESTING"] is True
    assert app.config["SQLALCHEMY_DATABASE_URI"] == "sqlite:///:memory:"


def test_health_check_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json == {"status": "ok"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_app_factory.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app'`

- [ ] **Step 3: Create `app/config.py`**

```python
# app/config.py
"""Environment-based configuration classes.

No secrets or environment-specific values are hardcoded elsewhere in the
codebase; every module reads settings from `current_app.config`, which is
populated from one of these classes based on FLASK_ENV / the app factory
argument.
"""
import os

basedir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


class BaseConfig:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key-change-me")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SESSION_TIMEOUT_MINUTES = int(os.environ.get("SESSION_TIMEOUT_MINUTES", 30))
    ACCOUNT_LOCKOUT_ATTEMPTS = int(os.environ.get("ACCOUNT_LOCKOUT_ATTEMPTS", 5))
    CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0")
    CELERY_RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", "redis://localhost:6379/1")
    WTF_CSRF_ENABLED = True


class DevelopmentConfig(BaseConfig):
    DEBUG = True
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", f"sqlite:///{os.path.join(basedir, 'instance', 'fms_dev.sqlite')}"
    )


class TestingConfig(BaseConfig):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"


class ProductionConfig(BaseConfig):
    DEBUG = False
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL")


config_by_name = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}
```

- [ ] **Step 4: Create `app/extensions.py`**

```python
# app/extensions.py
"""Single source of truth for Flask extension instances.

Instances are created here (unbound) and initialized against the app in
create_app(), so any module can `from app.extensions import db` without
circular imports.
"""
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

- [ ] **Step 5: Create `app/__init__.py`**

```python
# app/__init__.py
"""Application factory."""
from flask import Flask, jsonify

from app.config import config_by_name
from app.extensions import db, migrate, login_manager, csrf


def create_app(config_name="development"):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(config_by_name[config_name])

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)

    @app.route("/health")
    def health_check():
        return jsonify({"status": "ok"})

    return app
```

- [ ] **Step 6: Create `wsgi.py`**

```python
# wsgi.py
from app import create_app

app = create_app("production")

if __name__ == "__main__":
    app.run()
```

- [ ] **Step 7: Run test to verify it passes**

Run: `pytest tests/unit/test_app_factory.py -v`
Expected: PASS (2 passed)

- [ ] **Step 8: Commit**

```bash
git add app/__init__.py app/config.py app/extensions.py wsgi.py tests/unit/test_app_factory.py
git commit -m "feat: app factory, environment-based config, extensions init"
```

---

## Task 2: BaseModel Mixin & AuditLog Model

**Files:**
- Create: `app/core/__init__.py`
- Create: `app/core/models/__init__.py`
- Create: `app/core/models/base.py`
- Create: `app/core/models/audit_log.py`
- Test: `tests/unit/test_base_model.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_base_model.py
import pytest
from app import create_app
from app.extensions import db
from app.core.models.base import BaseModel
from app.core.models.audit_log import AuditLog


class SampleThing(BaseModel):
    __tablename__ = "sample_thing"
    name = db.Column(db.String(80), nullable=False)


@pytest.fixture
def app():
    app = create_app("testing")
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def test_base_model_has_audit_columns(app):
    thing = SampleThing(name="widget")
    db.session.add(thing)
    db.session.commit()

    assert thing.id is not None
    assert thing.created_at is not None
    assert thing.updated_at is not None
    assert thing.is_active is True
    assert thing.created_by is None
    assert thing.updated_by is None


def test_audit_log_model_columns(app):
    log = AuditLog(
        table_name="sample_thing",
        record_id=1,
        action="CREATE",
        old_values=None,
        new_values={"name": "widget"},
        user_id=None,
        ip_address="127.0.0.1",
    )
    db.session.add(log)
    db.session.commit()

    assert log.id is not None
    assert log.timestamp is not None
    assert log.action == "CREATE"
    assert log.new_values == {"name": "widget"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_base_model.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.core'`

- [ ] **Step 3: Create `app/core/__init__.py` and `app/core/models/__init__.py`** (both empty)

```bash
mkdir -p app/core/models
touch app/core/__init__.py app/core/models/__init__.py
```

- [ ] **Step 4: Create `app/core/models/base.py`**

```python
# app/core/models/base.py
"""Base model mixin providing consistent audit columns and soft-delete
across every table in the system. Master Data (Phase 2) and Transaction
Modules (Phase 3) must maintain complete history, so this convention is
established from the foundation rather than retrofitted later.
"""
from datetime import datetime, timezone

from app.extensions import db


def _utcnow():
    return datetime.now(timezone.utc)


class BaseModel(db.Model):
    __abstract__ = True

    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=_utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    updated_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
```

- [ ] **Step 5: Create `app/core/models/audit_log.py`**

```python
# app/core/models/audit_log.py
"""Generic Audit Trail record. Populated automatically by AuditService
(app/core/audit/audit_service.py) via a SQLAlchemy before_flush listener —
no module needs to write to this table directly.
"""
from datetime import datetime, timezone

from app.extensions import db


class AuditLog(db.Model):
    __tablename__ = "audit_log"

    id = db.Column(db.Integer, primary_key=True)
    table_name = db.Column(db.String(100), nullable=False, index=True)
    record_id = db.Column(db.Integer, nullable=False, index=True)
    action = db.Column(db.String(20), nullable=False)  # CREATE / UPDATE / DELETE
    old_values = db.Column(db.JSON, nullable=True)
    new_values = db.Column(db.JSON, nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    timestamp = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    ip_address = db.Column(db.String(45), nullable=True)
```

- [ ] **Step 6: Run test to verify it passes**

Run: `pytest tests/unit/test_base_model.py -v`
Expected: PASS (2 passed). Note: `BaseModel.created_by`/`updated_by` reference `user.id`, which
doesn't exist yet — SQLite defers FK resolution until you actually enforce constraints, so this
passes now; Task 5 creates the real `User` table with `__tablename__ = "user"`.

- [ ] **Step 7: Commit**

```bash
git add app/core/__init__.py app/core/models/ tests/unit/test_base_model.py
git commit -m "feat: BaseModel audit-column mixin and AuditLog model"
```

---

## Task 3: BaseRepository (Generic CRUD)

**Files:**
- Create: `app/core/repository/__init__.py`
- Create: `app/core/repository/base_repository.py`
- Test: `tests/unit/test_base_repository.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_base_repository.py
import pytest
from app import create_app
from app.extensions import db
from app.core.models.base import BaseModel
from app.core.repository.base_repository import BaseRepository


class Widget(BaseModel):
    __tablename__ = "widget"
    name = db.Column(db.String(80), nullable=False)


class WidgetRepository(BaseRepository):
    model = Widget


@pytest.fixture
def app():
    app = create_app("testing")
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def repo(app):
    return WidgetRepository()


def test_create_and_get_by_id(repo):
    widget = repo.create(name="Bolt")
    fetched = repo.get_by_id(widget.id)
    assert fetched is not None
    assert fetched.name == "Bolt"


def test_list_returns_only_active_by_default(repo):
    repo.create(name="Active One")
    inactive = repo.create(name="Inactive One")
    repo.soft_delete(inactive.id)

    results = repo.list()
    names = [w.name for w in results]
    assert "Active One" in names
    assert "Inactive One" not in names


def test_list_with_filters(repo):
    repo.create(name="Alpha")
    repo.create(name="Beta")

    results = repo.list(name="Alpha")
    assert len(results) == 1
    assert results[0].name == "Alpha"


def test_update(repo):
    widget = repo.create(name="Original")
    updated = repo.update(widget.id, name="Renamed")
    assert updated.name == "Renamed"
    assert repo.get_by_id(widget.id).name == "Renamed"


def test_soft_delete_sets_is_active_false(repo):
    widget = repo.create(name="ToDelete")
    repo.soft_delete(widget.id)
    fetched = repo.get_by_id(widget.id)
    assert fetched.is_active is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_base_repository.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.core.repository'`

- [ ] **Step 3: Create `app/core/repository/__init__.py`** (empty)

```bash
mkdir -p app/core/repository
touch app/core/repository/__init__.py
```

- [ ] **Step 4: Create `app/core/repository/base_repository.py`**

```python
# app/core/repository/base_repository.py
"""Generic CRUD repository. Every module's repository subclasses this and
sets `model = <SomeModel>`, satisfying the Repository Pattern requirement
without repeating boilerplate CRUD code per module.
"""
from app.extensions import db


class BaseRepository:
    model = None  # subclasses must set this to a BaseModel subclass

    def get_by_id(self, record_id, include_inactive=False):
        query = self.model.query.filter_by(id=record_id)
        if not include_inactive:
            query = query.filter_by(is_active=True)
        return query.first()

    def list(self, include_inactive=False, **filters):
        query = self.model.query
        if not include_inactive:
            query = query.filter_by(is_active=True)
        if filters:
            query = query.filter_by(**filters)
        return query.all()

    def create(self, **kwargs):
        instance = self.model(**kwargs)
        db.session.add(instance)
        db.session.commit()
        return instance

    def update(self, record_id, **kwargs):
        instance = self.get_by_id(record_id, include_inactive=True)
        if instance is None:
            return None
        for key, value in kwargs.items():
            setattr(instance, key, value)
        db.session.commit()
        return instance

    def soft_delete(self, record_id):
        instance = self.get_by_id(record_id, include_inactive=True)
        if instance is None:
            return None
        instance.is_active = False
        db.session.commit()
        return instance
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/unit/test_base_repository.py -v`
Expected: PASS (5 passed)

- [ ] **Step 6: Commit**

```bash
git add app/core/repository/ tests/unit/test_base_repository.py
git commit -m "feat: generic BaseRepository (CRUD + soft delete)"
```

---

## Task 4: Password Hashing Utility

**Files:**
- Create: `app/core/security/__init__.py`
- Create: `app/core/security/password.py`
- Test: `tests/unit/test_password.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_password.py
from app.core.security.password import hash_password, verify_password


def test_hash_password_returns_different_string_than_input():
    hashed = hash_password("MySecret123!")
    assert hashed != "MySecret123!"
    assert hashed.startswith("$argon2")


def test_verify_password_correct():
    hashed = hash_password("MySecret123!")
    assert verify_password("MySecret123!", hashed) is True


def test_verify_password_incorrect():
    hashed = hash_password("MySecret123!")
    assert verify_password("WrongPassword", hashed) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_password.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.core.security'`

- [ ] **Step 3: Create `app/core/security/__init__.py`** (empty)

```bash
mkdir -p app/core/security
touch app/core/security/__init__.py
```

- [ ] **Step 4: Create `app/core/security/password.py`**

```python
# app/core/security/password.py
"""Password hashing via Argon2 (the current OWASP-recommended default),
satisfying the "Password Encryption" security requirement.
"""
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

_hasher = PasswordHasher()


def hash_password(plain_password: str) -> str:
    return _hasher.hash(plain_password)


def verify_password(plain_password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, plain_password)
    except VerifyMismatchError:
        return False
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/unit/test_password.py -v`
Expected: PASS (3 passed)

- [ ] **Step 6: Commit**

```bash
git add app/core/security/__init__.py app/core/security/password.py tests/unit/test_password.py
git commit -m "feat: Argon2 password hashing utility"
```

---

## Task 5: User, Role, Permission Models

**Files:**
- Create: `app/modules/__init__.py`
- Create: `app/modules/user_management/__init__.py`
- Create: `app/modules/user_management/models.py`
- Test: `tests/unit/test_user_management_models.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_user_management_models.py
import pytest
from app import create_app
from app.extensions import db
from app.modules.user_management.models import User, Role, Permission


@pytest.fixture
def app():
    app = create_app("testing")
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def test_create_user(app):
    user = User(
        username="jdela_cruz",
        email="jdela_cruz@example.com",
        password_hash="hashed",
        first_name="Juan",
        last_name="Dela Cruz",
    )
    db.session.add(user)
    db.session.commit()

    assert user.id is not None
    assert user.is_active is True
    assert user.failed_login_attempts == 0
    assert user.must_change_password is False


def test_role_permission_many_to_many(app):
    role = Role(name="Fleet Admin", description="Full fleet access")
    perm = Permission(code="vehicle.create", module="vehicle", action="create",
                       description="Create vehicles")
    role.permissions.append(perm)
    db.session.add(role)
    db.session.commit()

    assert perm in role.permissions
    assert role in perm.roles


def test_user_role_many_to_many_supports_multiple_roles(app):
    user = User(username="mtan", email="mtan@example.com", password_hash="hashed",
                first_name="Maria", last_name="Tan")
    role1 = Role(name="Dispatcher")
    role2 = Role(name="Approver")
    user.roles.append(role1)
    user.roles.append(role2)
    db.session.add(user)
    db.session.commit()

    assert len(user.roles) == 2
    assert role1 in user.roles and role2 in user.roles
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_user_management_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.modules'`

- [ ] **Step 3: Create package inits**

```bash
mkdir -p app/modules/user_management
touch app/modules/__init__.py app/modules/user_management/__init__.py
```

- [ ] **Step 4: Create `app/modules/user_management/models.py`**

```python
# app/modules/user_management/models.py
"""User, Role, Permission and their association tables.
Permission granularity is module + action grain (e.g. "vehicle.create"),
per the Phase 1a design decision.
"""
from datetime import datetime, timezone

from app.extensions import db
from app.core.models.base import BaseModel

# Association tables (plain many-to-many, no extra columns needed yet)
role_permissions = db.Table(
    "role_permission",
    db.Column("role_id", db.Integer, db.ForeignKey("role.id"), primary_key=True),
    db.Column("permission_id", db.Integer, db.ForeignKey("permission.id"), primary_key=True),
)

user_roles = db.Table(
    "user_role",
    db.Column("user_id", db.Integer, db.ForeignKey("user.id"), primary_key=True),
    db.Column("role_id", db.Integer, db.ForeignKey("role.id"), primary_key=True),
)


class User(BaseModel):
    __tablename__ = "user"

    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    first_name = db.Column(db.String(80), nullable=False)
    last_name = db.Column(db.String(80), nullable=False)
    branch_id = db.Column(db.Integer, nullable=True)  # FK to Branch (Master Data, Phase 2)
    last_login_at = db.Column(db.DateTime, nullable=True)
    failed_login_attempts = db.Column(db.Integer, default=0, nullable=False)
    must_change_password = db.Column(db.Boolean, default=False, nullable=False)

    roles = db.relationship("Role", secondary=user_roles, back_populates="users")

    # Flask-Login required properties/methods
    @property
    def is_authenticated(self):
        return True

    @property
    def is_anonymous(self):
        return False

    def get_id(self):
        return str(self.id)


class Role(BaseModel):
    __tablename__ = "role"

    name = db.Column(db.String(80), unique=True, nullable=False)
    description = db.Column(db.String(255), nullable=True)
    is_system_role = db.Column(db.Boolean, default=False, nullable=False)

    permissions = db.relationship("Permission", secondary=role_permissions, back_populates="roles")
    users = db.relationship("User", secondary=user_roles, back_populates="roles")


class Permission(BaseModel):
    __tablename__ = "permission"

    code = db.Column(db.String(120), unique=True, nullable=False, index=True)  # e.g. "vehicle.create"
    module = db.Column(db.String(80), nullable=False, index=True)
    action = db.Column(db.String(40), nullable=False)
    description = db.Column(db.String(255), nullable=True)

    roles = db.relationship("Role", secondary=role_permissions, back_populates="permissions")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/unit/test_user_management_models.py -v`
Expected: PASS (3 passed)

- [ ] **Step 6: Commit**

```bash
git add app/modules/__init__.py app/modules/user_management/__init__.py \
        app/modules/user_management/models.py tests/unit/test_user_management_models.py
git commit -m "feat: User, Role, Permission models with RBAC associations"
```

---

## Task 6: User/Role/Permission Repositories

**Files:**
- Create: `app/modules/user_management/repository.py`
- Test: `tests/unit/test_user_management_repository.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_user_management_repository.py
import pytest
from app import create_app
from app.extensions import db
from app.modules.user_management.repository import UserRepository, RoleRepository, PermissionRepository


@pytest.fixture
def app():
    app = create_app("testing")
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def test_user_repository_get_by_username(app):
    repo = UserRepository()
    repo.create(username="admin", email="admin@example.com", password_hash="x",
                first_name="Admin", last_name="User")

    found = repo.get_by_username("admin")
    assert found is not None
    assert found.email == "admin@example.com"

    assert repo.get_by_username("nobody") is None


def test_role_repository_get_by_name(app):
    repo = RoleRepository()
    repo.create(name="Approver")

    found = repo.get_by_name("Approver")
    assert found is not None


def test_permission_repository_get_by_code(app):
    repo = PermissionRepository()
    repo.create(code="user.create", module="user", action="create")

    found = repo.get_by_code("user.create")
    assert found is not None
    assert found.module == "user"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_user_management_repository.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.modules.user_management.repository'`

- [ ] **Step 3: Create `app/modules/user_management/repository.py`**

```python
# app/modules/user_management/repository.py
from app.core.repository.base_repository import BaseRepository
from app.modules.user_management.models import User, Role, Permission


class UserRepository(BaseRepository):
    model = User

    def get_by_username(self, username):
        return User.query.filter_by(username=username, is_active=True).first()

    def get_by_email(self, email):
        return User.query.filter_by(email=email, is_active=True).first()


class RoleRepository(BaseRepository):
    model = Role

    def get_by_name(self, name):
        return Role.query.filter_by(name=name, is_active=True).first()


class PermissionRepository(BaseRepository):
    model = Permission

    def get_by_code(self, code):
        return Permission.query.filter_by(code=code, is_active=True).first()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_user_management_repository.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add app/modules/user_management/repository.py tests/unit/test_user_management_repository.py
git commit -m "feat: User/Role/Permission repositories"
```

---

## Task 7: AuditService (Automatic Audit Trail)

**Files:**
- Create: `app/core/audit/__init__.py`
- Create: `app/core/audit/audit_service.py`
- Modify: `app/__init__.py`
- Test: `tests/unit/test_audit_service.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_audit_service.py
import pytest
from app import create_app
from app.extensions import db
from app.core.models.audit_log import AuditLog
from app.modules.user_management.models import User


@pytest.fixture
def app():
    app = create_app("testing")
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def test_creating_a_row_writes_an_audit_log(app):
    user = User(username="alice", email="alice@example.com", password_hash="x",
                first_name="Alice", last_name="A")
    db.session.add(user)
    db.session.commit()

    logs = AuditLog.query.filter_by(table_name="user", action="CREATE").all()
    assert len(logs) == 1
    assert logs[0].record_id == user.id
    assert logs[0].new_values["username"] == "alice"


def test_updating_a_row_writes_an_audit_log_with_old_and_new_values(app):
    user = User(username="bob", email="bob@example.com", password_hash="x",
                first_name="Bob", last_name="B")
    db.session.add(user)
    db.session.commit()

    user.first_name = "Robert"
    db.session.commit()

    logs = AuditLog.query.filter_by(table_name="user", action="UPDATE").all()
    assert len(logs) == 1
    assert logs[0].old_values["first_name"] == "Bob"
    assert logs[0].new_values["first_name"] == "Robert"


def test_soft_deleting_a_row_writes_an_update_audit_log(app):
    user = User(username="carol", email="carol@example.com", password_hash="x",
                first_name="Carol", last_name="C")
    db.session.add(user)
    db.session.commit()

    user.is_active = False
    db.session.commit()

    logs = AuditLog.query.filter_by(table_name="user", action="UPDATE").all()
    assert any(log.new_values.get("is_active") is False for log in logs)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_audit_service.py -v`
Expected: FAIL — `test_creating_a_row_writes_an_audit_log` fails because no AuditLog rows exist yet
(listener not registered).

- [ ] **Step 3: Create `app/core/audit/__init__.py`** (empty)

```bash
mkdir -p app/core/audit
touch app/core/audit/__init__.py
```

- [ ] **Step 4: Create `app/core/audit/audit_service.py`**

```python
# app/core/audit/audit_service.py
"""Generic Audit Trail writer.

Hooked via a SQLAlchemy `before_flush` event listener on the session, so
every insert/update/delete of any BaseModel subclass automatically writes
an AuditLog row — no per-module code required. This is what makes Audit
Trail a true cross-cutting concern rather than something each module has
to remember to call.
"""
from flask import has_request_context, request
from flask_login import current_user

from app.core.models.base import BaseModel
from app.core.models.audit_log import AuditLog


def _current_user_id():
    if has_request_context():
        try:
            if current_user and current_user.is_authenticated:
                return current_user.id
        except Exception:
            return None
    return None


def _current_ip():
    if has_request_context():
        return request.remote_addr
    return None


def _serialize(instance, columns):
    result = {}
    for column in columns:
        value = getattr(instance, column.name)
        # JSON-safe values only (datetimes -> isoformat)
        if hasattr(value, "isoformat"):
            value = value.isoformat()
        result[column.name] = value
    return result


def _record_changes(session):
    new_logs = []

    for instance in session.new:
        if isinstance(instance, BaseModel) and not isinstance(instance, AuditLog):
            new_logs.append({
                "table_name": instance.__tablename__,
                "action": "CREATE",
                "old_values": None,
                "new_values": instance,  # resolved to dict after flush assigns the PK
            })

    for instance in session.dirty:
        if isinstance(instance, BaseModel) and not isinstance(instance, AuditLog):
            state = session.is_modified(instance, include_collections=False)
            if not state:
                continue
            history_old = {}
            history_new = {}
            for attr in instance.__mapper__.column_attrs:
                hist = attr.load_history() if hasattr(attr, "load_history") else None
            # Use SQLAlchemy's attribute history API via inspect
            from sqlalchemy import inspect as sa_inspect
            insp = sa_inspect(instance)
            for attr in insp.mapper.column_attrs:
                hist = insp.attrs[attr.key].history
                if hist.has_changes():
                    old_val = hist.deleted[0] if hist.deleted else None
                    new_val = hist.added[0] if hist.added else getattr(instance, attr.key)
                    if hasattr(old_val, "isoformat"):
                        old_val = old_val.isoformat()
                    if hasattr(new_val, "isoformat"):
                        new_val = new_val.isoformat()
                    history_old[attr.key] = old_val
                    history_new[attr.key] = new_val
            if history_new:
                new_logs.append({
                    "table_name": instance.__tablename__,
                    "action": "UPDATE",
                    "old_values": history_old,
                    "new_values": history_new,
                    "record_id": instance.id,
                })

    return new_logs


def register_audit_listeners(db):
    """Call once at app startup (in the app factory) to enable auditing."""
    from sqlalchemy import event

    @event.listens_for(db.session.__class__, "before_flush")
    def before_flush(session, flush_context, instances):
        pending = _record_changes(session)
        # Stash on the session; we resolve CREATE record_ids after flush.
        session.info["_pending_audit_logs"] = session.info.get("_pending_audit_logs", []) + pending

    @event.listens_for(db.session.__class__, "after_flush")
    def after_flush(session, flush_context):
        pending = session.info.pop("_pending_audit_logs", [])
        if not pending:
            return
        user_id = _current_user_id()
        ip_address = _current_ip()
        for entry in pending:
            if entry["action"] == "CREATE":
                instance = entry["new_values"]
                columns = instance.__mapper__.columns
                log = AuditLog(
                    table_name=entry["table_name"],
                    record_id=instance.id,
                    action="CREATE",
                    old_values=None,
                    new_values=_serialize(instance, columns),
                    user_id=user_id,
                    ip_address=ip_address,
                )
            else:
                log = AuditLog(
                    table_name=entry["table_name"],
                    record_id=entry["record_id"],
                    action=entry["action"],
                    old_values=entry["old_values"],
                    new_values=entry["new_values"],
                    user_id=user_id,
                    ip_address=ip_address,
                )
            session.add(log)
        session.flush()
```

- [ ] **Step 5: Register the listener in the app factory**

```python
# app/__init__.py — add import and one call inside create_app(), after db.init_app(app)
from app.core.audit.audit_service import register_audit_listeners
```

Add this line right after `db.init_app(app)`:

```python
    with app.app_context():
        register_audit_listeners(db)
```

- [ ] **Step 6: Run test to verify it passes**

Run: `pytest tests/unit/test_audit_service.py -v`
Expected: PASS (3 passed)

- [ ] **Step 7: Run full test suite to check for regressions**

Run: `pytest -v`
Expected: All previous tests still PASS

- [ ] **Step 8: Commit**

```bash
git add app/core/audit/ app/__init__.py tests/unit/test_audit_service.py
git commit -m "feat: automatic Audit Trail via SQLAlchemy before/after_flush listeners"
```

---

## Task 8: PermissionRegistry & require_permission Decorator

**Files:**
- Create: `app/core/security/permission_registry.py`
- Create: `app/core/security/decorators.py`
- Test: `tests/unit/test_permission_registry.py`
- Test: `tests/unit/test_require_permission_decorator.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_permission_registry.py
from app.core.security.permission_registry import PermissionRegistry


def test_register_and_list_permissions():
    registry = PermissionRegistry()
    registry.register("user", [
        ("user.create", "create", "Create users"),
        ("user.edit", "edit", "Edit users"),
    ])

    codes = [p["code"] for p in registry.all_permissions()]
    assert "user.create" in codes
    assert "user.edit" in codes


def test_register_is_idempotent_per_module():
    registry = PermissionRegistry()
    registry.register("user", [("user.create", "create", "Create users")])
    registry.register("user", [("user.create", "create", "Create users")])

    codes = [p["code"] for p in registry.all_permissions()]
    assert codes.count("user.create") == 1
```

```python
# tests/unit/test_require_permission_decorator.py
import pytest
from flask import Flask
from flask_login import LoginManager, login_user

from app import create_app
from app.extensions import db
from app.modules.user_management.models import User, Role, Permission
from app.core.security.decorators import require_permission


@pytest.fixture
def app():
    app = create_app("testing")

    @app.route("/protected")
    @require_permission("user.create")
    def protected():
        return "ok"

    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def _make_user_with_permission(code):
    perm = Permission(code=code, module=code.split(".")[0], action=code.split(".")[1])
    role = Role(name="TestRole")
    role.permissions.append(perm)
    user = User(username="u1", email="u1@example.com", password_hash="x",
                first_name="U", last_name="One")
    user.roles.append(role)
    db.session.add(user)
    db.session.commit()
    return user


def test_request_without_login_redirects_to_login(app):
    client = app.test_client()
    response = client.get("/protected")
    assert response.status_code in (302, 401)


def test_user_with_permission_gets_200(app):
    with app.app_context():
        user = _make_user_with_permission("user.create")
        user_id = user.id

    client = app.test_client()
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True

    response = client.get("/protected")
    assert response.status_code == 200


def test_user_without_permission_gets_403(app):
    with app.app_context():
        user = _make_user_with_permission("other.permission")
        user_id = user.id

    client = app.test_client()
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True

    response = client.get("/protected")
    assert response.status_code == 403
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_permission_registry.py tests/unit/test_require_permission_decorator.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Create `app/core/security/permission_registry.py`**

```python
# app/core/security/permission_registry.py
"""Central registry that modules use to declare the permissions they need.

Each module calls `permission_registry.register(<module_name>, [(code,
action, description), ...])` at import time. `seed_permissions()` (called
from a CLI command in Task 18) syncs the registry into the Permission
table, so permission codes always stay in sync with the code that checks
them — no manual DB entry required per feature.
"""


class PermissionRegistry:
    def __init__(self):
        self._permissions = {}  # code -> dict

    def register(self, module, entries):
        for code, action, description in entries:
            self._permissions[code] = {
                "code": code,
                "module": module,
                "action": action,
                "description": description,
            }

    def all_permissions(self):
        return list(self._permissions.values())


# Module-level singleton shared across the app
permission_registry = PermissionRegistry()
```

- [ ] **Step 4: Create `app/core/security/decorators.py`**

```python
# app/core/security/decorators.py
"""@require_permission decorator: gates a route behind RBAC.

Checks the current user's roles -> permissions. Returns 403 if the user
lacks the permission, and lets Flask-Login's @login_required semantics
handle unauthenticated requests (redirect to login).
"""
from functools import wraps

from flask import abort
from flask_login import current_user, login_required


def require_permission(permission_code):
    def decorator(view_func):
        @wraps(view_func)
        @login_required
        def wrapped(*args, **kwargs):
            user_codes = {
                perm.code
                for role in current_user.roles
                for perm in role.permissions
            }
            if permission_code not in user_codes:
                abort(403)
            return view_func(*args, **kwargs)
        return wrapped
    return decorator
```

- [ ] **Step 5: Wire the Flask-Login `user_loader` (needed for the decorator test to log a user in)**

Add to `app/__init__.py`, inside `create_app()`, after `login_manager.init_app(app)`:

```python
    from app.modules.user_management.models import User

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/unit/test_permission_registry.py tests/unit/test_require_permission_decorator.py -v`
Expected: PASS (5 passed)

- [ ] **Step 7: Run full suite for regressions**

Run: `pytest -v`
Expected: All passing

- [ ] **Step 8: Commit**

```bash
git add app/core/security/permission_registry.py app/core/security/decorators.py app/__init__.py \
        tests/unit/test_permission_registry.py tests/unit/test_require_permission_decorator.py
git commit -m "feat: PermissionRegistry and require_permission RBAC decorator"
```

---

## Task 9: Auth Module (Login/Logout, Account Lockout)

**Files:**
- Create: `app/modules/auth/__init__.py`
- Create: `app/modules/auth/forms.py`
- Create: `app/modules/auth/routes.py`
- Create: `app/modules/auth/templates/auth/login.html`
- Modify: `app/__init__.py` (register blueprint)
- Test: `tests/integration/test_auth_flow.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_auth_flow.py
import pytest
from app import create_app
from app.extensions import db
from app.modules.user_management.models import User
from app.core.security.password import hash_password


@pytest.fixture
def app():
    app = create_app("testing")
    with app.app_context():
        db.create_all()
        user = User(
            username="loginuser", email="loginuser@example.com",
            password_hash=hash_password("CorrectHorse123!"),
            first_name="Login", last_name="User",
        )
        db.session.add(user)
        db.session.commit()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def test_login_with_correct_credentials_redirects_to_dashboard(client):
    response = client.post("/auth/login", data={
        "username": "loginuser", "password": "CorrectHorse123!"
    })
    assert response.status_code == 302
    assert response.headers["Location"] == "/"


def test_login_with_wrong_password_shows_error(client):
    response = client.post("/auth/login", data={
        "username": "loginuser", "password": "WrongPassword"
    }, follow_redirects=True)
    assert response.status_code == 200
    assert b"Invalid username or password" in response.data


def test_account_locks_after_max_failed_attempts(app, client):
    for _ in range(5):
        client.post("/auth/login", data={"username": "loginuser", "password": "wrong"})

    with app.app_context():
        user = User.query.filter_by(username="loginuser").first()
        assert user.failed_login_attempts >= 5

    response = client.post("/auth/login", data={
        "username": "loginuser", "password": "CorrectHorse123!"
    }, follow_redirects=True)
    assert b"account is locked" in response.data.lower()


def test_logout_requires_login_then_clears_session(client):
    client.post("/auth/login", data={
        "username": "loginuser", "password": "CorrectHorse123!"
    })
    response = client.get("/auth/logout")
    assert response.status_code == 302
    assert response.headers["Location"] == "/auth/login"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/integration/test_auth_flow.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.modules.auth'`

- [ ] **Step 3: Create package + forms**

```bash
mkdir -p app/modules/auth/templates/auth
touch app/modules/auth/__init__.py
```

```python
# app/modules/auth/forms.py
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, BooleanField
from wtforms.validators import DataRequired


class LoginForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired()])
    password = PasswordField("Password", validators=[DataRequired()])
    remember_me = BooleanField("Remember Me")
```

- [ ] **Step 4: Create `app/modules/auth/routes.py`**

```python
# app/modules/auth/routes.py
"""Session-based auth (Flask-Login). API/JWT auth is deferred to Phase 6
per the Phase 1a design decision.
"""
from flask import Blueprint, render_template, redirect, url_for, flash, current_app
from flask_login import login_user, logout_user, login_required

from app.modules.auth.forms import LoginForm
from app.modules.user_management.repository import UserRepository
from app.core.security.password import verify_password
from app.extensions import db

auth_bp = Blueprint("auth", __name__, url_prefix="/auth", template_folder="templates")

user_repository = UserRepository()


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    form = LoginForm()
    if form.validate_on_submit():
        user = user_repository.get_by_username(form.username.data)
        max_attempts = current_app.config["ACCOUNT_LOCKOUT_ATTEMPTS"]

        if user is None:
            flash("Invalid username or password", "danger")
            return render_template("auth/login.html", form=form)

        if user.failed_login_attempts >= max_attempts:
            flash("Your account is locked due to too many failed attempts. Contact an administrator.",
                  "danger")
            return render_template("auth/login.html", form=form)

        if verify_password(form.password.data, user.password_hash):
            user.failed_login_attempts = 0
            db.session.commit()
            login_user(user, remember=form.remember_me.data)
            return redirect(url_for("dashboard"))
        else:
            user.failed_login_attempts += 1
            db.session.commit()
            flash("Invalid username or password", "danger")

    return render_template("auth/login.html", form=form)


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))
```

- [ ] **Step 5: Create `app/modules/auth/templates/auth/login.html`**

```html
{# app/modules/auth/templates/auth/login.html #}
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>FMS — Login</title>
  <link href="https://cdnjs.cloudflare.com/ajax/libs/twitter-bootstrap/5.3.3/css/bootstrap.min.css" rel="stylesheet">
</head>
<body class="bg-light">
  <div class="container d-flex align-items-center justify-content-center" style="min-height:100vh;">
    <div class="card shadow-sm" style="width: 380px;">
      <div class="card-body p-4">
        <h4 class="card-title mb-3 text-center">Fleet Management System</h4>
        {% with messages = get_flashed_messages(with_categories=true) %}
          {% for category, message in messages %}
            <div class="alert alert-{{ category }}">{{ message }}</div>
          {% endfor %}
        {% endwith %}
        <form method="post">
          {{ form.hidden_tag() }}
          <div class="mb-3">
            {{ form.username.label(class_="form-label") }}
            {{ form.username(class_="form-control") }}
          </div>
          <div class="mb-3">
            {{ form.password.label(class_="form-label") }}
            {{ form.password(class_="form-control") }}
          </div>
          <div class="form-check mb-3">
            {{ form.remember_me(class_="form-check-input") }}
            {{ form.remember_me.label(class_="form-check-label") }}
          </div>
          <button type="submit" class="btn btn-primary w-100">Log In</button>
        </form>
      </div>
    </div>
  </div>
</body>
</html>
```

- [ ] **Step 6: Register the blueprint and a placeholder `/` dashboard route in `app/__init__.py`**

Add near the bottom of `create_app()`, before `return app`:

```python
    from app.modules.auth.routes import auth_bp
    app.register_blueprint(auth_bp)

    from flask_login import login_required

    @app.route("/")
    @login_required
    def dashboard():
        return "Dashboard placeholder"
```

- [ ] **Step 7: Run test to verify it passes**

Run: `pytest tests/integration/test_auth_flow.py -v`
Expected: PASS (4 passed)

- [ ] **Step 8: Run full suite for regressions**

Run: `pytest -v`
Expected: All passing

- [ ] **Step 9: Commit**

```bash
git add app/modules/auth/ app/__init__.py tests/integration/test_auth_flow.py
git commit -m "feat: session-based auth (login/logout) with account lockout"
```

---

## Task 10: User Management Service, Schemas & Routes

**Files:**
- Create: `app/modules/user_management/schemas.py`
- Create: `app/modules/user_management/service.py`
- Create: `app/modules/user_management/forms.py`
- Create: `app/modules/user_management/routes.py`
- Modify: `app/__init__.py` (register blueprint, register user_management permissions)
- Test: `tests/unit/test_user_management_service.py`
- Test: `tests/integration/test_user_management_routes.py`

- [ ] **Step 1: Write the failing unit test for the service**

```python
# tests/unit/test_user_management_service.py
import pytest
from app import create_app
from app.extensions import db
from app.modules.user_management.service import UserService
from app.modules.user_management.models import Role


@pytest.fixture
def app():
    app = create_app("testing")
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def test_create_user_hashes_password(app):
    service = UserService()
    user = service.create_user(
        username="newuser", email="newuser@example.com", password="PlainPass123!",
        first_name="New", last_name="User",
    )
    assert user.password_hash != "PlainPass123!"


def test_create_user_rejects_duplicate_username(app):
    service = UserService()
    service.create_user(username="dupe", email="a@example.com", password="Pass123!",
                         first_name="A", last_name="A")
    with pytest.raises(ValueError, match="username already exists"):
        service.create_user(username="dupe", email="b@example.com", password="Pass123!",
                             first_name="B", last_name="B")


def test_assign_roles_to_user(app):
    service = UserService()
    user = service.create_user(username="withroles", email="wr@example.com",
                                password="Pass123!", first_name="W", last_name="R")
    role = Role(name="Dispatcher")
    db.session.add(role)
    db.session.commit()

    service.assign_roles(user.id, [role.id])
    assert role in user.roles
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_user_management_service.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Create `app/modules/user_management/schemas.py`**

```python
# app/modules/user_management/schemas.py
from marshmallow import Schema, fields


class UserSchema(Schema):
    id = fields.Int(dump_only=True)
    username = fields.Str(required=True)
    email = fields.Email(required=True)
    first_name = fields.Str(required=True)
    last_name = fields.Str(required=True)
    is_active = fields.Bool(dump_only=True)
    created_at = fields.DateTime(dump_only=True)


class RoleSchema(Schema):
    id = fields.Int(dump_only=True)
    name = fields.Str(required=True)
    description = fields.Str()
    is_system_role = fields.Bool(dump_only=True)
```

- [ ] **Step 4: Create `app/modules/user_management/service.py`**

```python
# app/modules/user_management/service.py
"""Business logic for User/Role/Permission management. Routes must not
contain business logic — this is where it lives, per the "no business
logic in controllers/routes" requirement.
"""
from app.extensions import db
from app.core.security.password import hash_password
from app.modules.user_management.repository import UserRepository, RoleRepository
from app.modules.user_management.models import User, Role


class UserService:
    def __init__(self):
        self.user_repo = UserRepository()
        self.role_repo = RoleRepository()

    def create_user(self, username, email, password, first_name, last_name):
        if self.user_repo.get_by_username(username):
            raise ValueError("A user with this username already exists")
        if self.user_repo.get_by_email(email):
            raise ValueError("A user with this email already exists")

        return self.user_repo.create(
            username=username,
            email=email,
            password_hash=hash_password(password),
            first_name=first_name,
            last_name=last_name,
        )

    def update_user(self, user_id, **fields):
        if "password" in fields:
            fields["password_hash"] = hash_password(fields.pop("password"))
        return self.user_repo.update(user_id, **fields)

    def deactivate_user(self, user_id):
        return self.user_repo.soft_delete(user_id)

    def assign_roles(self, user_id, role_ids):
        user = self.user_repo.get_by_id(user_id)
        if user is None:
            raise ValueError("User not found")
        roles = Role.query.filter(Role.id.in_(role_ids)).all()
        user.roles = roles
        db.session.commit()
        return user

    def list_users(self):
        return self.user_repo.list()
