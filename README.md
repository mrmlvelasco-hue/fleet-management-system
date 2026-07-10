# FMS — Enterprise Fleet Management System

Phase 1a foundation: Flask app factory, RBAC (users/roles/permissions),
session auth with lockout, automatic audit trail, Enterprise UI shell.

Phase 1b core engines: Document Type Maintenance, generic Auto Numbering
Engine (configurable format, yearly/monthly/never reset, concurrency-safe
counters), Approval Path/Matrix maintenance, and the generic Approval Engine
runtime (Document Type → Amount Range → Matrix → Path → Levels; unlimited
levels; Submit/Approve/Reject/Return/Cancel; event hooks for the upcoming
Notification Engine).

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
