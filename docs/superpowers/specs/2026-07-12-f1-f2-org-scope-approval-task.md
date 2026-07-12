# F1 + F2 — Org-Scoped Approval Resolution & Generic Approval Task Inbox

**Date:** 2026-07-12
**Status:** Approved

## Problem
Approval eligibility today checks Role membership only. A "Fleet Manager"
role held by users in both Manila and Cebu branches would let a Cebu Fleet
Manager approve a Manila transaction — wrong. There's also no centralized
place to see "what's waiting for my action" — each module needs its own
pending-approval query.

## F1 — Org-Scoped Approval Resolution

**UserOrgScope** (new): user_id (FK), scope_type (BRANCH|BUSINESS_UNIT|
COMPANY|GLOBAL), branch_id (nullable FK), business_unit_id (nullable FK).
A user can have multiple rows (multi-branch access). GLOBAL/COMPANY scope
means no branch/BU restriction — approves anything of that role.

**ApprovalInstance** gets two new nullable columns: branch_id,
business_unit_id — the organizational context of the transaction being
approved, supplied by the submitting module at submit() time (same pattern
as the existing `amount` field). NULL means "no org context" — approval
falls back to today's role-only behavior for full backward compatibility.

**Engine change**: `_check_eligible()` for ROLE-type levels now also checks
that the acting user's UserOrgScope covers `instance.branch_id`/
`business_unit_id` (any GLOBAL/COMPANY scope always passes; a BRANCH-scoped
user must match; a BUSINESS_UNIT-scoped user must match). If
`instance.branch_id` is None, scope is not checked (legacy/no-context
transactions still resolve by role alone).

**Auto-inference**: `BaseTransactionService` (used by most transaction
modules) automatically infers `branch_id` from the record being submitted
— checking `record.vehicle.branch_id`, then `record.branch_id`, then
`record.department.branch_id`, whichever exists — and passes it to
`engine.submit()` without requiring every module to be touched
individually. Modules can still override by passing branch_id explicitly.

## F2 — Generic Approval Task / Inbox

**ApprovalTask** (new): approval_instance_id (FK), level_number,
document_type_id, document_number (denormalized for fast listing),
reference_table, reference_id, assigned_role_id (nullable),
assigned_user_id (nullable), branch_id/business_unit_id (denormalized from
instance), status (PENDING|COMPLETED|CANCELLED), requested_by,
created_at, completed_at, completed_by.

Engine lifecycle hooks (fully automatic — no per-module changes needed):
- `submit()` creates a PENDING ApprovalTask for level 1 if the document
  requires approval.
- `approve()` marks the current level's task COMPLETED, creates a new
  PENDING task for the next level (or none, if this was the final level).
- `reject()`/`return_document()`/`cancel()` mark the current level's task
  COMPLETED and cancel any other PENDING tasks for that instance.

**ApprovalTaskService.list_for_user(user)** — the single query behind "For
My Action": PENDING tasks where `assigned_user_id == user.id` OR
(`assigned_role_id` in the user's roles AND org-scope covers the task's
branch/BU). This is the reusable, generic worklist query — any future
approval-enabled module gets it for free.

## Dashboard Widget — "For My Action"

Modeled on the reference screenshot: a clickable list, most-recent/oldest
sortable, showing Document Number + Type, Subject/description, Requestor +
submitted date, current level, waiting time (aging), with a pending-count
badge. Clicking a row navigates to that document's own detail page — no
new "unified view" is built in this pass (that's F4); the existing
Approve/Reject/Return buttons on each module's detail page are already
permission- and eligibility-gated, so this satisfies "approver can click
through and act; everyone else simply doesn't see it or can't act."

## Testing
Unit: UserOrgScope CRUD, engine eligibility with/without scope match,
backward compatibility when no org context is set, ApprovalTask lifecycle
(created/completed/cancelled through submit→approve→approve final,
submit→reject, submit→cancel), list_for_user resolving correctly across
ROLE+scope and USER-type levels. Integration: dashboard widget shows only
eligible pending items, clicking through links to the right detail page,
users without matching scope don't see out-of-scope items.

## Out of Scope (deferred to F3/F4)
Unified approval document view, discussion/comment thread, Request
Additional Information action, Documents-Under-Review / Comments-Trend
widgets from the reference screenshot (those track a different, broader
"all documents in my org" view — can follow once F1/F2 are solid).
