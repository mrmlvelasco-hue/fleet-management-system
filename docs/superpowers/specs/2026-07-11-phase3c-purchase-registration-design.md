# Phase 3c — Purchase Requests & Vehicle Registration — Design Spec

**Date:** 2026-07-11
**Status:** Approved
**Phase:** 3 — Transaction Modules — sub-phase 3c of 3 (final transaction sub-phase)

## Scope
- Purchase Requests (the one document type that actually exercises the
  Approval Matrix's amount-range logic — PR has a real `amount` field)
- Vehicle Registration / Enrollment (Philippine LTO rules: 3-year registration
  for new vehicles, Conduction Number before Plate Number, renewal reminders)

## Data Models

**PurchaseRequest** (`app/modules/transactions/purchase_request/`)
document_number, requested_by (FK User), department_id (FK, nullable),
vendor_id (FK, nullable — suggested vendor), amount (Numeric 18,2 — drives
Approval Matrix amount-range resolution), description, justification,
needed_by_date, status (DRAFT/PENDING/APPROVED/ORDERED/RECEIVED/REJECTED/
RETURNED/CANCELLED), approval_instance_id.
**PurchaseRequestLine**: pr_id (FK), item_description, quantity, unit_cost
(Numeric), line_total (Numeric, computed = quantity × unit_cost). Amount on
the parent PR = sum of line totals (kept in sync on line add/update/remove).

**VehicleRegistration** (`app/modules/transactions/vehicle_registration/`)
document_number, vehicle_id (FK), registration_type (NEW|RENEWAL），
or_number (Official Receipt number, nullable until paid), cr_number
(Certificate of Registration number, nullable), plate_number (nullable —
becomes set on this record and pushed to Vehicle.plate_number when NEW
registration completes), registration_date, expiry_date (registration_date +
3 years for NEW, or +1 year... — actually LTO private-vehicle registration
in the Philippines is a flat annual renewal after the initial 3-year window
for new vehicles; I model both: `validity_years` field defaults 3 for NEW,
1 for RENEWAL, expiry_date computed accordingly), or_cr_cost (Numeric,
nullable), status (DRAFT/PENDING/APPROVED/COMPLETED/CANCELLED),
requested_by, approval_instance_id.

All on BaseModel — audit trail automatic, soft delete.

## Services

**PurchaseRequestService**
- `create(...)` with a list of line items — computes amount as sum of lines,
  generates document_number.
- `add_line/update_line/remove_line` — recompute PR.amount after each change
  (only while status == DRAFT; raises once submitted).
- `submit/approve/reject/return/cancel` — shared BaseTransactionService;
  amount is passed to `ApprovalEngine.submit(..., amount=pr.amount, ...)` so
  the existing Matrix amount-range resolution actually gets exercised here
  for the first time in a real transaction module.
- `mark_ordered()`, `mark_received()` — physical lifecycle after approval.

**VehicleRegistrationService**
- `create(...)` — validates: NEW registration requires the vehicle to not
  already have an active (non-expired, non-cancelled) registration; RENEWAL
  requires an existing prior registration for that vehicle. Computes
  expiry_date = registration_date + validity_years (default 3 for NEW, 1 for
  RENEWAL, both overridable).
- `submit/approve/reject/return/cancel` — shared base.
- `complete(or_number, cr_number, plate_number=None)` — on completion, if
  NEW and plate_number given, calls `VehicleService.assign_plate()` (already
  exists from Phase 2) to transition the vehicle from conduction number to
  plate number — directly satisfying the "Conduction Number before Plate
  Number" LTO rule from the master prompt.
- `get_expiring_registrations(days_ahead)` — for renewal reminders (mirrors
  PMDueCalculationService's pattern); wires into the Notification Engine via
  a `registration_expiring`/`registration_expired` event, reusing the same
  auto-generation-task pattern as PM (a `tasks.py` with an idempotency check
  against existing open RENEWAL registrations).

## UI
Same list/form/detail/print pattern as prior transaction modules.
- PurchaseRequest form: dynamic add/remove line-item rows (same JS pattern as
  Approval Path levels / PM Scope items), live-computed total shown before
  submit (client-side calculation refined server-side).
- VehicleRegistration form: registration_type toggle, conditional OR/CR fields
  shown only on the complete action (not at creation, since those numbers
  don't exist yet).
- Vehicle detail page: add a **Registration History** tab (mirrors the
  Maintenance History tab from 3b).

Permissions: purchaserequest.*, vehicleregistration.*.

## Testing
Unit: PR line management + amount recomputation, submit passes correct
amount into ApprovalEngine (verifies Matrix amount-range resolution actually
picks the right path for different amounts), mark_ordered/received lifecycle;
VehicleRegistration NEW/RENEWAL validation rules, expiry computation,
complete() plate assignment integration with VehicleService, expiring-soon
detection + auto reminder task idempotency. Integration: CRUD + permission
403s, dynamic line-item form, print views, vehicle registration history tab.

## Out of Scope (3c)
This closes out Phase 3 entirely — Phase 4 (Dashboard incl. "My Actions" and
"Vehicles Due for Maintenance" widgets) is next per the master prompt's phase
plan.
