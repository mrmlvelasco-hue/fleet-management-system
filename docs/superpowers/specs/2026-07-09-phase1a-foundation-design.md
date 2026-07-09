# Phase 1a — Foundation: Architecture, Auth & RBAC — Design Spec

**Date:** 2026-07-09
**Status:** Approved
**Parent project:** Enterprise Fleet Management System (FMS)
**Phase:** 1 (Folder Structure, Database Design, System Administration Module) — sub-phase 1a of 3 (1a Foundation → 1b Core Engines → 1c Remaining System Admin)

## Context

The FMS master prompt (see `Enterprise Fleet Management System (FMS) - PROMPT.docx`) defines a large,
multi-phase enterprise application (ERP/EAM/CMMS style) covering System Administration, Master Data,
and 10 Transaction Modules. Given the scale, Phase 1 (System Administration) is further decomposed into:

- **1a — Foundation** (this spec): base architecture, folder structure, Auth, User/Role/Permission
  Management (RBAC core), Audit Trail infrastructure, UI shell.
- **1b — Core Engines**: Document Type Maintenance, Auto Numbering Engine, Approval Matrix/Path
  Maintenance.
- **1c — Remaining System Admin**: System Parameters, Lookup Maintenance, Email Templates,
  Notification Rules, Dashboard Configuration, Company Profile, Backup & Restore Configuration,
  Report Configuration.

Each sub-phase gets its own spec → plan → build → review cycle.

## Decisions Made

| Decision | Choice |
|---|---|
| Runtime location | Built in-container as a git repo; user downloads files/zip |
| Local dev database | SQLite (dev/test) with MySQL-compatible SQLAlchemy models for production |
| Auth scope (1a) | Session-based (Flask-Login) only; API/JWT auth deferred to Phase 6 (REST API) |
| Permission granularity | Module + action grain (e.g. `vehicle.create`, `vehicle.approve`, `tripticket.print`) |
| UI shell | Full Enterprise UI shell (sidebar, top nav, breadcrumbs, dark mode, login page) built now, reused by all later modules |
| Celery/Redis | Wired up now (empty task queue, ready for Notification Engine later) |
| Code organization | Feature-first (`app/modules/<feature>/`) with a `core/` package for cross-cutting engines (Approval, Notification, Audit, Auto-Numbering) |

## Folder Structure

```
fms/
├── app/
│   ├── __init__.py                # App factory
│   ├── config.py                  # Environment-based config (Dev/Test/Prod)
│   ├── extensions.py              # db, login_manager, migrate, csrf, celery init
│   ├── core/                      # Cross-cutting engines & shared infra
│   │   ├── models/
│   │   │   ├── base.py            # Base model mixin: id, created_at, updated_at, created_by, updated_by, is_active
│   │   │   └── audit_log.py
│   │   ├── audit/
│   │   │   └── audit_service.py   # Generic audit trail writer, used by all modules
│   │   ├── security/
│   │   │   ├── decorators.py      # @require_permission('user.create')
│   │   │   └── password.py        # Hashing (Argon2/bcrypt)
│   │   ├── repository/
│   │   │   └── base_repository.py # Generic CRUD repository base class
│   │   └── celery_app.py
│   ├── modules/
│   │   ├── auth/
│   │   │   ├── routes.py
│   │   │   ├── forms.py
│   │   │   └── templates/
│   │   └── user_management/       # Users, Roles, Permissions
│   │       ├── models.py
│   │       ├── repository.py
│   │       ├── service.py
│   │       ├── routes.py
│   │       ├── forms.py
│   │       ├── schemas.py         # Marshmallow
│   │       └── templates/
│   ├── templates/
│   │   ├── layout/                # base.html, sidebar, topnav, dashboard shell
│   │   └── errors/                # 403/404/500
│   ├── static/
│   │   ├── css/                   # Bootstrap 5, custom, dark-mode.css
│   │   └── js/
│   └── cli.py                     # seed commands (create admin, seed roles)
├── migrations/                    # Flask-Migrate
├── tests/
│   ├── unit/
│   └── integration/
├── instance/                      # local sqlite db, gitignored
├── .env.example
├── requirements.txt
├── celery_worker.py
├── wsgi.py
└── README.md
```

## Data Model (1a scope)

- **User**: id, username, email, password_hash, first_name, last_name, branch_id (FK, nullable for now),
  is_active, last_login_at, failed_login_attempts, must_change_password
- **Role**: id, name, description, is_system_role
- **Permission**: id, code (e.g. `user.create`), module, action, description
- **RolePermission**: role_id, permission_id (many-to-many)
- **UserRole**: user_id, role_id (many-to-many — supports multiple roles per user)
- **AuditLog**: id, table_name, record_id, action (CREATE/UPDATE/DELETE), old_values (JSON),
  new_values (JSON), user_id, timestamp, ip_address

All tables use the `BaseModel` mixin for consistent audit columns and soft-delete (`is_active`)
rather than hard deletes, since Master Data (later phase) must maintain complete history and this
convention should be established from the foundation.

## Components

- **BaseRepository**: generic `get_by_id`, `list`, `create`, `update`, `soft_delete`, with query
  filtering hooks. Every module's repository subclasses this (Repository Pattern requirement).
- **AuditService**: hooked via SQLAlchemy event listeners on `before_flush`, so every insert/update/
  delete across *any* model automatically writes an AuditLog entry with zero per-module code. This
  satisfies "Audit Trail" as a true cross-cutting concern rather than something each module must
  remember to call.
- **RBAC**: `@require_permission('user.create')` decorator checks the current user's roles →
  permissions. A `PermissionRegistry` seeds permission codes from each module at startup so
  permissions stay in sync with code (no manual DB entry required per feature).
- **Password/session security**: Argon2 hashing, configurable session timeout (system parameter
  in principle, but 1a hardcodes a sane default since the System Parameters module ships in 1c),
  CSRF via Flask-WTF, account lockout after N failed attempts.

## UI Shell

Base Jinja2 layout with collapsible sidebar, top nav (user menu, notifications bell placeholder,
dark-mode toggle persisted via cookie), breadcrumb block, and a dashboard landing page with empty
KPI card placeholders (to be filled in Phase 4). Login page styled per the Enterprise UI requirement.
User/Role/Permission management screens use DataTables + SweetAlert2 for confirmations + Select2
for role/permission multi-select, per the required frontend stack.

## Error Handling & Logging

Centralized error handlers (403/404/500) rendered via the same layout. Structured logging
(Python `logging` + rotating file handler, JSON formatter for future log aggregation) configured
per environment (Dev/Test/Prod config classes).

## Testing

Pytest with a SQLite in-memory test DB fixture. Unit tests for repository/service layers.
Integration tests for the auth flow and permission enforcement (e.g. asserting a user without
`user.create` receives a 403).

## Out of Scope for 1a

- Document Type, Auto Numbering, Approval Matrix/Path (→ 1b)
- System Parameters, Lookups, Email Templates, Notification Rules, Dashboard Config, Company
  Profile, Backup & Restore Config, Report Config (→ 1c)
- Master Data modules (→ Phase 2)
- Transaction modules (→ Phase 3)
- Actual dashboard KPIs, reports, REST API endpoints (→ Phases 4/5/6)
- API/JWT authentication (→ Phase 6)

## Open Risks / Notes

- SQLite vs MySQL type differences (e.g. `JSON`, `ENUM`) will be handled via SQLAlchemy generic
  types to preserve MySQL/MSSQL portability, per the "must support migration to Microsoft SQL
  Server" requirement.
- Session timeout is hardcoded in 1a and must be revisited to read from System Parameters once
  1c ships, to fully satisfy "no values shall be hardcoded."
