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

## 4. Phase 2 — other request documents (separate plan)

Lift the policy into `BaseTransactionService` (`editable_scope`,
`can_edit`, `changes_since_submission`, `edited_after_submission`
emission) and apply it to Purchase Request, Trip Ticket, Vehicle
Registration, Vehicle Movement, Tire and Battery transactions and ATD:
add the missing update endpoints and edit screens, move ATD and
Tire/Battery onto the shared `ApprovalWorkflow` + decision panel, and
add Return/Resubmit to Tire/Battery transactions (absent today).

## 5. Out of scope

Approval restart on edit (explicitly declined), cross-group type
change, editing Completed/Cancelled records.
