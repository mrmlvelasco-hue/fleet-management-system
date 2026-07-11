# Phase 3c — Purchase Requests & Vehicle Registration — Design Spec

**Date:** 2026-07-11
**Status:** Approved
**Phase:** 3 — Transaction Modules — sub-phase 3c of 3 (final)

## Scope
- Purchase Request (PR) — first module to exercise the Approval Matrix's
  amount-range resolution end-to-end (built in 1b, unused until now)
- Vehicle Registration — LTO rules: 3-year new registration, conduction→plate
  transition, annual renewal, expiry reminders via existing Notification Engine

## Data Models

**PurchaseRequest**: document_number, requested_by (FK User), department_id
(FK, nullable), purpose, needed_by_date, total_amount (Numeric 18,2 —
computed from line items), vendor_id (FK, nullable), status (DRAFT/PENDING/
APPROVED/REJECTED/RETURNED/CANCELLED/CLOSED), approval_instance_id.

**PurchaseRequestItem**: request_id (FK), item_description, quantity
(Numeric), unit_of_measure, estimated_unit_cost (Numeric), sort_order.
estimated_total is computed (quantity * estimated_unit_cost), not stored.

**VehicleRegistration**: document_number, vehicle_id (FK), registration_type
(NEW|RENEWAL), or_number, cr_number, plate_number (nullable — set on
complete()), registration_date, expiry_date (computed: +3 years for NEW,
+1 year for RENEWAL), status (DRAFT/PENDING/APPROVED/COMPLETED/CANCELLED),
requested_by, approval_instance_id.

All on BaseModel — audit trail automatic, soft delete.

## Services

**PurchaseRequestService** (extends BaseTransactionService)
- `create(items, ...)` — creates PR + PurchaseRequestItem rows, computes
  `total_amount` = sum(quantity * estimated_unit_cost). NEW: `submit()`
  override passes `amount=self.total_amount` to ApprovalEngine.submit() —
  this is the first module where amount actually varies and the Approval
  Matrix's min/max bracket resolution gets exercised for real.
- `close(pr_id)` — CLOSED status once goods/services received (requires
  APPROVED first).

**VehicleRegistrationService** (extends BaseTransactionService)
- `create(...)` — NEW registration requires vehicle to have a
  conduction_number (raises ConductionNumberRequiredError otherwise, per
  master prompt "Conduction Number before Plate Number").
- `complete(plate_number)` — sets Vehicle.plate_number (conduction→plate
  transition), computes expiry_date (registration_date + 3y for NEW, +1y for
  RENEWAL), status COMPLETED.
- Expiring-soon detection: `get_expiring_registrations(within_days)` —
  same pattern as PMDueCalculationService; a scheduled task
  (`registration_expiry_check_task`, idempotent) fires `registration_expiry`
  notifications for registrations expiring within N days
  (SystemParameter `REGISTRATION_EXPIRY_WARNING_DAYS`, default 30).

## UI
Same list/form/detail/print pattern as 3a/3b. PR form has dynamic add/remove
line-item rows (JS pattern reused from PM Scope Template). Sidebar extends
the existing Transactions group.

Permissions: purchaserequest.view/create/update/delete/print,
vehicleregistration.view/create/update/delete/print.

## Testing
Unit: PR total_amount computation from items; Approval Matrix resolving
different paths for different amount brackets (proves amount-range feature
end-to-end); Vehicle Registration conduction-required validation; plate
transition + expiry date math (NEW vs RENEWAL); expiring-soon detection +
idempotent notification task. Integration: CRUD + submit/approve flow,
dynamic line-item form, print views, permission 403s.

## Out of Scope
Actual LTO system integration (this stays a system-of-record only), PDF
generation (Phase 5), remaining backlog items (Custom Fields, My Actions
widget, comments — post-Phase-8 / Phase 4 as previously agreed).
