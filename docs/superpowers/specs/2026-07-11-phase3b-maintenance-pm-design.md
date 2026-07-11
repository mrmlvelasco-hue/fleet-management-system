# Phase 3b — Maintenance Cluster (incl. PM Scheduling) — Design Spec

**Date:** 2026-07-11
**Status:** Approved
**Phase:** 3 — Transaction Modules — sub-phase 3b of 3

## Scope
- 3b-1: PM Configuration (schedules, scope templates)
- 3b-2: Maintenance Order transaction (Preventive + Corrective, checklist execution)
- 3b-4: Tire & Battery Management transactions
- (3b-3 due-detection service ships here; the dashboard widget UI itself is
  deferred to Phase 4 alongside "My Actions" — see backlog.md)

## Data Models

**PMSchedule** (`app/modules/maintenance_config/`)
vehicle_type_id (FK VehicleType, nullable — NULL = applies to all types),
maintenance_type_id (FK MaintenanceType), trigger_mode (KM|CALENDAR|HYBRID),
interval_km (nullable), interval_days (nullable), priority (LOW|MEDIUM|HIGH),
is_active. Hybrid = whichever comes first (both intervals set, service due
when either is reached).

**PMScopeTemplate**
maintenance_type_id (FK), name, description.
**PMScopeItem**: template_id (FK), activity_code, activity_description,
standard_labor_hours (Numeric nullable), estimated_cost (Numeric nullable),
required_parts (Text), vendor_recommendation (String nullable), sort_order.

**MaintenanceOrder** (`app/modules/transactions/maintenance_order/`)
document_number, vehicle_id (FK), maintenance_type_id (FK), category
(PREVENTIVE|CORRECTIVE — copied from MaintenanceType at creation),
pm_schedule_id (FK nullable — set when auto/manually generated from a
schedule), scope_template_id (FK nullable), description, odometer_at_service,
scheduled_date, completed_date (nullable), assigned_mechanic (String — free
text for now; FK to a Mechanic master is out of scope), vendor_id (FK
nullable), estimated_cost, actual_cost (nullable), status (DRAFT/PENDING/
APPROVED/IN_PROGRESS/COMPLETED/CANCELLED), requested_by, approval_instance_id.

**MaintenanceChecklistItem**
order_id (FK MaintenanceOrder), activity_code, activity_description,
is_done (bool), done_by (FK User nullable), done_at (nullable), sort_order.
Generated from the linked PMScopeTemplate when the order is created (copy,
not a live reference — so historical checklists don't change if the
template is edited later).

**TireTransaction** / **BatteryTransaction** (`app/modules/transactions/tire_txn/`,
`battery_txn/`) — as previously scoped: document_number, tire_id/battery_id
(FK), vehicle_id (FK nullable), action (MOUNT|DISMOUNT|RETREAD|DISPOSE for
tire; MOUNT|DISMOUNT|DISPOSE for battery), transaction_date,
odometer_at_service (tire only), remarks, status, requested_by,
approval_instance_id.

All on BaseModel — audit trail automatic, soft delete.

## Services

**PMScheduleService / PMScopeTemplateService** — standard CRUD (create/
update/deactivate), scope template items managed as a set (replace-on-update,
same pattern as ApprovalLevel).

**MaintenanceOrderService**
- `create(...)` — if pm_schedule_id given, auto-populates scope_template_id
  from the schedule's maintenance_type, generates document_number, creates
  MaintenanceChecklistItem rows copied from the scope template.
- `submit/approve/reject/return/cancel` — shared BaseTransactionService.
- `start_work()` → IN_PROGRESS.
- `toggle_checklist_item(item_id, done, user)` — only while IN_PROGRESS.
- `complete(actual_cost, completed_date)` — requires all checklist items
  done (raises IncompleteChecklistError otherwise) for PREVENTIVE orders;
  CORRECTIVE orders have no checklist requirement. Updates
  `Vehicle.current_odometer` if odometer_at_service is higher, and
  (important for due-detection) is what "resets the clock" for next-due
  calculation.

**TireTransactionService / BatteryTransactionService** — as scoped in the
earlier design: MOUNT sets tire/battery status + vehicle link, DISMOUNT
frees back to stock, DISPOSE deactivates the part record.

**PMDueCalculationService** (`app/core/maintenance/`)
`get_due_status(vehicle) -> {schedule, status, next_due_km, next_due_date}`
per applicable PMSchedule (matched by vehicle_type_id, falling back to
NULL/all-types schedules). Status = GOOD|DUE_SOON|OVERDUE using: KM trigger
→ compare current_odometer to (last_service_odometer + interval_km); CALENDAR
trigger → compare today to (last_service_date + interval_days); HYBRID →
worst-case (whichever triggers first). "Due soon" threshold: within 500km or
within 30 days (matches your spec), configurable via SystemParameters
`PM_DUE_SOON_KM` / `PM_DUE_SOON_DAYS`. `last_service` is the most recent
COMPLETED MaintenanceOrder for that vehicle+maintenance_type (or vehicle's
acquisition data if none yet).
`get_all_due_vehicles() -> list` — used by the dashboard widget (Phase 4) and
by the scheduled auto-generation task.

**Notification event codes** (new, wired into existing Notification Engine):
`pm_due_soon`, `pm_overdue`, `maintenance_completed` — engine dispatch calls
added at the point PMDueCalculationService flags a vehicle (via the daily
Celery task) and at MaintenanceOrderService.complete().

**Auto-generation**: `app/modules/transactions/maintenance_order/tasks.py`
Celery beat task (daily) — for each OVERDUE/DUE_SOON vehicle without an
existing open (non-COMPLETED/CANCELLED) MaintenanceOrder for that
maintenance_type, auto-creates a DRAFT MaintenanceOrder from the schedule and
fires `pm_due_soon`/`pm_overdue` notifications. (Celery beat scheduling
config itself — actually running on a timer — is an infra/deployment concern
noted in README; the task function and its unit tests ship now.)

## UI
- PM Schedules: CRUD screen (System Administration → PM Configuration)
- PM Scope Templates: CRUD with dynamic scope-item rows (same pattern as
  Approval Path's dynamic levels)
- Maintenance Orders: list/form/detail/print, checklist tab on detail page
  (checkboxes, disabled unless IN_PROGRESS and user has update permission)
- Tire/Battery Transactions: list/form/detail/print
- Vehicle detail page: new "Maintenance History" tab listing completed
  MaintenanceOrders for that vehicle (date, WO number, PM type, cost, vendor)

Permissions: pmschedule.*, pmscopetemplate.*, maintenanceorder.*,
tiretxn.*, batterytxn.*.

## Testing
Unit: PM schedule CRUD, scope template item management, due-calculation for
KM/CALENDAR/HYBRID triggers at GOOD/DUE_SOON/OVERDUE boundaries, maintenance
order creation from schedule (checklist copy), checklist completion gating,
complete() blocked with incomplete checklist (preventive) vs allowed
(corrective), tire/battery mount/dismount status sync, auto-generation task
idempotency (doesn't duplicate an open order). Integration: CRUD screens +
permission 403s, checklist toggle endpoint, vehicle maintenance history tab,
print views.

## Out of Scope (3b)
Dashboard widget UI (Phase 4, alongside "My Actions"), actual Celery beat
schedule wiring in production (task ships, cron config is deployment doc),
Mechanic master data (assigned_mechanic stays free-text for now),
transaction commenting (separate backlog item, may pull forward per
priority discussion).
