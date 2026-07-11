# FMS — Enterprise Fleet Management System

Phase 1a foundation: Flask app factory, RBAC (users/roles/permissions),
session auth with lockout, automatic audit trail, Enterprise UI shell.

Phase 1b core engines: Document Type Maintenance, generic Auto Numbering
Engine (configurable format, yearly/monthly/never reset, concurrency-safe
counters), Approval Path/Matrix maintenance, and the generic Approval Engine
runtime (Document Type → Amount Range → Matrix → Path → Levels; unlimited
levels; Submit/Approve/Reject/Return/Cancel; event hooks for the upcoming
Notification Engine).

Phase 1c remaining System Administration: System Parameters, Lookup
Maintenance, Company Profile, Email Templates, Notification Rules + the
Notification Engine (in-app + Celery-queued email), Audit Trail viewer,
Dashboard Config, Backup/Report Config.

Phase 2 Master Data: Branch/Department/Business Unit, Vehicle/Maintenance
Types, Vehicle/Driver/Tire/Battery/Vendor masters, with a generic
Attachment service (multi-file upload, image preview, download) reused by
every module.

Phase 3a Transaction Modules: Trip Ticket (with the driver-from-master
toggle), Authority To Drive, Vehicle Movement — all auto-numbered, routed
through the Approval Engine, audited automatically, with a simple
printable HTML view (browser print-to-PDF).

Phase 3b Maintenance Cluster: PM Schedules (KM/Calendar/Hybrid "whichever
comes first" triggers) and PM Scope Templates (checklists) configure how
and when preventive maintenance is due; Maintenance Orders (Preventive +
Corrective) generate a checklist from the scope template and gate
completion until all items are done; Tire and Battery Transactions
(mount/dismount/retread/dispose) keep master status in sync; a
PMDueCalculationService computes GOOD/DUE_SOON/OVERDUE per vehicle and an
idempotent auto-generation task creates draft Maintenance Orders + fires
pm_due_soon/pm_overdue notifications for due vehicles. Vehicle detail page
now has a Maintenance History tab.

Phase 3c Procurement & Registration (completes Phase 3 — Transaction
Modules): Purchase Requests with dynamic line items whose summed amount
drives real Approval Matrix amount-range routing (small vs. large requests
go to different approvers); Vehicle Registration with Philippine LTO rules
(3-year validity for NEW, 1-year for RENEWAL, Conduction Number → Plate
Number transition on completion, expiring-registration detection). Vehicle
detail page now also has a Registration History tab.

See `docs/superpowers/test-scripts/` for manual QA scripts per phase.

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
