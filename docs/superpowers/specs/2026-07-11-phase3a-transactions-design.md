# Phase 3a — Transaction Modules: Trip Ticket, ATD, Vehicle Movement — Design Spec

**Date:** 2026-07-11
**Status:** Approved
**Phase:** 3 — Transaction Modules — sub-phase 3a of 3 (3a Foundational → 3b Maintenance → 3c Procurement/Registration)

## Scope
- Trip Ticket (with Require Driver From Master system parameter toggle)
- Authority To Drive (ATD)
- Vehicle Movement
- Printing: simple printable HTML view (browser print-to-PDF), not server-rendered PDF
  (that lands in Phase 5 — Reporting)

## Cross-cutting recipe (every transaction module follows this)
1. DocumentType row (already creatable via 1b UI) — TT / ATD / VM codes
2. NumberingScheme row — auto-numbering via existing AutoNumberingService
3. Submit through ApprovalEngine (existing 1b engine) — resolves matrix, walks levels
4. Attachments via existing AttachmentService (reference_table/reference_id pattern)
5. Notifications via existing NotificationEngine + NotificationRule config
6. Audit trail — automatic, zero code (1a)
7. Printable HTML view — `/print` route rendering a document-styled page, browser
   handles PDF via Ctrl+P / window.print()

## Data Models

**TripTicket**: document_number (unique, from AutoNumberingService), vehicle_id (FK),
driver_id (FK Driver, nullable — manual entry mode), driver_name_manual (nullable,
used when System Parameter REQUIRE_DRIVER_FROM_MASTER=NO), destination, purpose,
departure_datetime, expected_return_datetime, actual_return_datetime (nullable),
odometer_out, odometer_in (nullable), passengers (text), status (DRAFT/PENDING/
APPROVED/RELEASED/COMPLETED/REJECTED/RETURNED/CANCELLED), requested_by (FK User),
approval_instance_id (FK, nullable).

**AuthorityToDrive**: document_number, vehicle_id (FK), driver_id (FK Driver),
purpose, valid_from, valid_to, status (same pattern), requested_by, approval_instance_id.

**VehicleMovement**: document_number, vehicle_id (FK), movement_type (Lookup
MOVEMENT_TYPE: TRANSFER/DISPATCH/RETURN/OTHER), from_location, to_location,
movement_date, remarks, status, requested_by, approval_instance_id.

All on BaseModel (audit trail automatic, soft delete for history).

## Services

Each module: `<Module>Service.create(**fields, user)` → generates document_number via
AutoNumberingService, creates record status=DRAFT, then `submit(id, user)` → calls
ApprovalEngine.submit(doc_type_code, table, id, amount=None, user) → stores
approval_instance_id, sets status=PENDING (or APPROVED if doc type has
requires_approval=False). `approve/reject/return_document/cancel` on the service
delegate to ApprovalEngine and sync the local status field via the engine's event
(engine already fires events; module subscribes via on_event to update its own status
column — this keeps modules decoupled from engine internals, per spec's "no approval
logic in modules" principle).

**TripTicketService** additionally: `_resolve_driver()` — reads
SystemParameterService.get("REQUIRE_DRIVER_FROM_MASTER"); if YES requires driver_id
from Driver Master, if NO accepts driver_name_manual with no master record created
(spec requirement, verbatim from master prompt).

## UI

List (DataTables) → New/Edit form → Detail page with action buttons (Submit/Approve/
Reject/Return/Cancel, permission-gated + eligibility-checked by engine) → Print view
(separate route, minimal chrome, `window.print()` button, uses Company Profile for
letterhead).

Permissions: tripticket.view/create/update/delete/print, atd.*, vehiclemovement.*.
Approval actions gated by existing ApprovalEngine eligibility (role/user per level) —
no separate "approve" permission needed, consistent with 1b engine design.

Sidebar group: **Transactions** (new collapsible group, same pattern as 1c).

## Testing
Unit: document creation + auto-numbering integration, driver resolution (master vs
manual), submit/approve/reject/return/cancel state sync via engine events, movement
type validation. Integration: CRUD + submit + approve flow end-to-end per module,
print view renders, permission 403s.

## Out of scope (3a)
Server-rendered PDF (Phase 5), mobile endpoints (Phase 6), the other 7 transaction
modules (3b/3c).
