# Phase 3a — Transaction Modules: Trip Ticket, ATD, Vehicle Movement — Design Spec

**Date:** 2026-07-11
**Status:** Approved
**Phase:** 3 — Transaction Modules, sub-phase 3a of 3 (3a Foundational → 3b Maintenance → 3c Procurement/Registration)

## Scope
- Trip Ticket (with driver-from-master toggle per SystemParameter `REQUIRE_DRIVER_FROM_MASTER`)
- Authority To Drive (ATD)
- Vehicle Movement
- Printing: simple printable HTML view (browser print-to-PDF), no server-side PDF generation this phase

## Cross-cutting recipe (same for every transaction module)
1. Model on BaseModel (audit trail automatic from 1a)
2. Document number generated via `AutoNumberingService.generate(code)` on submit
3. Approval via `ApprovalEngine.submit/approve/reject/return_document/cancel`
4. Attachments via shared `AttachmentService` (reference_table/reference_id)
5. Notifications automatic — NotificationRule + NotificationEngine already wired to ApprovalEngine events
6. Status badge shown from `ApprovalInstance.status` (DRAFT/PENDING/APPROVED/REJECTED/RETURNED/CANCELLED)
7. Printable view: `/print` route rendering a print-friendly template (`@media print` CSS, browser's native print-to-PDF)
8. Permissions: `<module>.view/create/update/delete/submit/approve/print`

## Data Models

**TripTicket**: document_number (unique, nullable until submit), vehicle_id (FK),
driver_id (FK Driver, nullable), driver_name_manual (string, nullable — used when
`REQUIRE_DRIVER_FROM_MASTER`=NO), destination, purpose, departure_datetime,
return_datetime (nullable), odometer_out, odometer_in (nullable),
passengers (text, nullable), status (DRAFT/RELEASED/RETURNED/CANCELLED — separate
from approval status; tracks physical trip lifecycle), requested_by (FK User).

**AuthorityToDrive**: document_number, driver_id (FK), vehicle_id (FK), valid_from,
valid_to, purpose, restrictions (text, nullable), requested_by (FK User).

**VehicleMovement**: document_number, vehicle_id (FK), movement_type
(TRANSFER/DEPLOYMENT/RETRIEVAL — Lookup MOVEMENT_TYPE), from_branch_id (FK),
to_branch_id (FK), movement_date, reason, requested_by (FK User).

All three: `approval_instance_id` (FK, nullable until submitted) links to the
ApprovalEngine's ApprovalInstance for that document.

## Services

Each module: `<Module>Service.create/update/submit/approve/reject/return/cancel/deactivate`.
- `create()`: saves as DRAFT, no document number yet (per spec: numbering only on submit,
  matching real-world practice of not "burning" numbers on abandoned drafts)
- `submit()`: calls `AutoNumberingService.generate()` to assign document_number, then
  `ApprovalEngine.submit()` to create the ApprovalInstance; document type's
  `requires_approval` flag (already configurable from 1b) determines whether it needs
  levels or auto-approves
- `approve/reject/return/cancel()`: thin wrappers delegating to `ApprovalEngine`,
  then updating the module's own status field where relevant (e.g. Trip Ticket
  physical RELEASED status is separate from approval APPROVED status)
- Trip Ticket `create()`/`update()` branches on `SystemParameterService.get('REQUIRE_DRIVER_FROM_MASTER')`:
  YES → driver_id required, driver_name_manual ignored; NO → driver_name_manual required,
  driver_id nullable (no master record created, per master prompt)

## UI

List screen (DataTables) → Detail/Form screen → action buttons (Submit, Approve, Reject,
Return, Cancel, Print) shown conditionally based on permission + current status +
approval eligibility (reuse `current_user.has_permission` and a small eligibility check
against the current ApprovalLevel). Attachment panel reused from Phase 2. Print view is a
separate route/template with a "Print" button (`window.print()`), formatted like a formal
document with Company Profile letterhead (from 1c).

Sidebar group: **Transactions** — Trip Tickets, Authority to Drive, Vehicle Movement.

## Testing
Unit: create/submit/approve/reject/return/cancel flows per module; driver-from-master
toggle behavior; document number assignment only on submit; status transitions guarded
(e.g. can't approve a DRAFT). Integration: CRUD screens + permission 403s; submit →
approval instance created; print view renders 200.

## Out of Scope (3a)
Server-side PDF generation (browser print-to-PDF only, per your direction), maintenance
modules (3b), purchase requests/vehicle registration (3c), mobile endpoints, actual LTO
integration.
