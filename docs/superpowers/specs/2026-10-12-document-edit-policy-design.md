# Document Edit Policy — Maintenance Order first (Phase 1), all request documents next (Phase 2)

Date: 2026-10-12 · Status: Approved by Michael (mockup reviewed) · Branches: Flask `v258`, React `v199`

## 1. Problem

A user who raises a request cannot correct it once it has left the
very first Draft. On a Maintenance Order (MO):

* `editable_scope()` returns `NONE` while approval is **Pending** and
  `REMARKS_ONLY` once work is **In Progress**.
* The API's `PUT /maintenance-orders/<id>` allow-list silently drops
  `destination_branch_id`, `driver_id`, `disposal_value`,
  `disposal_recipient`, `scope_template_id` and `vehicle_id`, even
  though the service accepts most of them. A save returns 200 and
  nothing changes.
* React's edit panel (`MoCostEditor`) only sends description, cost,
  mechanic, scheduled date and vendor.
* Only holders of `maintenanceorder.update` can edit. The initiator
  of the order cannot, unless they also hold that permission.
* Transfer orders accept Destination = Origin, and Origin is a
  snapshot taken at creation that goes stale if the vehicle moves
  before completion (seen on MO-2026-000046: Origin Muntinlupa while
  the vehicle is at Manila Hub).

Other request documents are worse: Trip Ticket, Vehicle Registration,
Vehicle Movement and Tire/Battery transactions have **no edit at all**,
and a returned Purchase Request cannot be corrected.

## 2. Business rule (confirmed)

| Order / approval state | Editable | Side effect |
|---|---|---|
| Draft, never submitted | All fields | — |
| Draft, approval Returned | All fields | — |
| Draft, approval Pending | All fields | Notify approvers; approval keeps its place (no restart) |
| Draft, approval Approved (awaiting Start) / In Progress | All fields | Notify approvers |
| Completed, Cancelled, approval Rejected | Locked | — |

**Who may edit:** the initiator (`requested_by`) **or** any user holding
`<document>.update`, subject to the usual branch visibility.

**Approver protection:** because approval is not restarted, every edit
made after first submission is shown to anyone viewing the document as
a "Changed after submission" list (field, was, now, who, when), read
from the existing Audit Trail.

**Transaction type** may be changed only within its own group
(Transfer ↔ Relocation, Scrappage → Sold …). Moving to another group
means cancelling and raising a new order — a cross-group switch would
change the approval matrix, the number series (ATR/ADR) and the field
set at once.

## 3. Phase 1 — Maintenance Order

### 3.1 Flask

**Edit policy (`MaintenanceOrderService`)**
* `editable_scope(order)` returns `FULL` for every non-terminal state
  (`DRAFT` with no/Pending/Returned/Approved instance, `IN_PROGRESS`),
  `NONE` for `COMPLETED`, `CANCELLED`, or an instance that is
  `REJECTED`/`CANCELLED`-and-order-cancelled. `REMARKS_ONLY` is retired
  (React keeps tolerating it).
* New `can_edit(order, user)`: `editable_scope == FULL` and
  (`user.id == order.requested_by` or `user.has_permission("maintenanceorder.update")`).
* `update()` enforces `can_edit` when a user is given.

**Fields** — API allow-list = service allow-list:
`vehicle_id, transaction_type_id, maintenance_type_id, scope_template_id,
scheduled_date, odometer_at_service, estimated_cost, assigned_mechanic,
vendor_id, driver_id, assignment_classification, destination_branch_id,
disposal_value, disposal_recipient, description`. Integer ids and
Decimal amounts are coerced at the API boundary with field-named 400s.

**Validation in `update()`**
1. `transaction_type_id`: the new type must have the same
   `order_category` and `group` as the current one.
2. `vehicle_id` change on a Maintenance-category order re-runs the
   "vehicle already has an open order of this maintenance type" check
   (excluding this order).
3. Whenever the order has a destination branch, `origin_branch_id` is
   re-taken from the vehicle's current branch on every edit, and again
   at completion just before `vehicle.branch_id` moves.
4. `destination_branch_id` must differ from the (refreshed) origin.

**Notifications** — reuse the Notification Engine, no hard-coded recipients:
* New event code `edited_after_submission`, emitted by `update()` when
  the order has an approval instance in `PENDING` or `APPROVED`, or the
  order is `IN_PROGRESS`, and at least one field actually changed.
* New recipient type `PRIOR_APPROVERS` (users with an `APPROVE` action
  on the instance), alongside the existing `CURRENT_APPROVER`.
* Seed rules: `edited_after_submission` → `CURRENT_APPROVER` and
  `PRIOR_APPROVERS`, `IN_APP`. Admins can add EMAIL via System Admin.
* `NotificationEngine.dispatch` de-duplicates recipients per channel
  within one event, so a user matched by two rules gets one message.

**Changed-after-submission feed**
* `changes_since_submission(order)` reads `audit_logs` UPDATE rows for
  the order after the first `SUBMIT` action, keeps only the editable
  fields above, resolves ids to names (vehicle plate, branch, driver,
  vendor, transaction/maintenance type, template) and returns
  `[{field, label, old, new, changed_by, changed_at}]`, newest first.
* Detail JSON gains `can_edit` (for the requesting user) and
  `changes_since_submission`.

**Parts** follow the same scope (editable while `FULL`), except when a
Purchase Request has already been generated from the order — then
parts stay locked with a message pointing to the PR.

### 3.2 React

* `src/lib/mapping.ts`: `moFieldVisibility(orderCategory, group, code)`
  extracted from the create form, used by create **and** edit (same
  pattern as `vendorAppliesToOrder`).
* `MoEditForm` replaces `MoCostEditor`: shows exactly the fields the
  type showed at creation; Vehicle, Driver, Vendor and Destination
  Branch use the existing `SearchPicker` over `/reference/*`;
  Transaction Type lists only types of the same group; changed fields
  are highlighted; pending / in-progress notice explains who will be
  notified; Destination = Origin is blocked client-side as well.
* Edit button shown when `data.can_edit`.
* `ChangedAfterSubmission` panel (generic, reused in Phase 2) renders
  `changes_since_submission`.
* System Admin → Notification Rules: add `edited_after_submission`
  event and `PRIOR_APPROVERS` recipient options.

### 3.3 Tests (written first)

Flask: scope per state; initiator / permission holder / neither;
every field saves per group (Maintenance, Assignment, Transfer,
Disposal, Administrative, Accessories); cross-group type change
rejected; destination = origin rejected; origin refreshed; vehicle
duplicate check; notification sent on pending and in-progress edits,
not on draft; no notification when nothing changed; dedupe; changed
feed content and filtering; parts locked once a PR exists.
React: field set per group in the edit form; Edit button visibility;
pending notice; payload contents; changed-after-submission panel.

Mutation-check the permission gate and the scope rule.

## 4. Phase 2 — other request documents (2026-10-13)

**Shared policy in `BaseTransactionService`** (MO moves onto it too):
`EDITABLE_STATUSES` (default `("DRAFT",)`; MO `("DRAFT","IN_PROGRESS")`),
`update_permission`, `EDIT_FIELDS` (field → label, kind), and generic
`editable_scope`, `can_edit`, `update` (validate hook → value-compared
change detection → setattr → after-edit hook → notify),
`changes_since_submission`. One API helper coerces payloads by kind and
maps errors (403 forbidden / 400 field / 409 state).

**Open vs locked, per document — the MO rule for every document
(revised 2026-10-13).** Every request document follows the same
standard approval process as the Maintenance Order, so it follows the
same edit rule. A document is editable while it is Draft — unsubmitted,
Pending, Returned, or Approved but not yet actioned — **and while its
work is ongoing** (the document's equivalent of MO In Progress), and
not Rejected. It locks only once closed. Edits after submission never
restart approval; the current approver (Pending) or the approvers who
signed off (Approved / ongoing) are notified with the change list.

| Document | Editable statuses | Locked |
|---|---|---|
| Maintenance Order | DRAFT, IN_PROGRESS | COMPLETED, CANCELLED |
| Purchase Request (header and lines) | DRAFT, ORDERED | RECEIVED, CANCELLED |
| Trip Ticket | DRAFT, RELEASED | COMPLETED, CANCELLED |
| ATD | DRAFT, ACTIVE | EXPIRED, CANCELLED |
| Vehicle Movement | DRAFT, IN_TRANSIT | COMPLETED, CANCELLED |
| Vehicle Registration | DRAFT (no ongoing stage) | COMPLETED, CANCELLED |
| Tire / Battery Transaction | DRAFT (applied on approval) | COMPLETED, CANCELLED |

The first draft of Phase 2 locked documents once they left Draft; the
client corrected this the same day ("same approval path as standard
process, apply to all transactions"). Per-document `EDITABLE_STATUSES`
keeps it configurable.

**Editable fields** (the create-time fields):

| Document | Fields |
|---|---|
| Purchase Request | department, vendor, description, justification, needed-by date; lines (add/edit/remove) |
| Trip Ticket | vehicle, driver (or manual name), destination, purpose, departure, odometer out, passengers |
| Vehicle Registration | vehicle, NEW/RENEWAL type, registration date, validity years (expiry recomputed), OR/CR cost, odometer |
| Vehicle Movement | vehicle, movement type, from, to, date, start, driver, employee responsible, purpose, remarks |
| Tire Transaction | tire, action, vehicle, date, odometer, remarks |
| Battery Transaction | battery, action, vehicle, date, remarks |
| ATD | vehicle, driver, purpose, valid from/to, odometer out, linked MO |

Vehicle Registration's vehicle and NEW/RENEWAL type are editable like
the MO's vehicle: a change (or a new registration date) re-runs the
creation rules — a NEW is refused while the vehicle has another active
registration; a RENEWAL needs a prior registration or a migrated
last-known expiry — against the vehicle's *other* registrations, and
the error is shown on the field that moved.

Validation re-uses each `create()` rule: Trip Ticket driver-from-master
parameter, movement type lookup, tire/battery action list.

**Approval UI alignment.** ATD and Tire/Battery move onto the shared
`ApprovalWorkflow` + decision panel; Tire/Battery gain Return/Resubmit
endpoints (the service already inherits them). Every detail page shows
the Edit control by `can_edit` and the `ChangedAfterSubmission` list.

## 5. Out of scope

Approval restart on edit (explicitly declined), cross-group type
change, editing Completed/Cancelled records.
