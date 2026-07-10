# Phase 1b — Core Engines: Document Types, Auto Numbering, Approval Engine — Design Spec

**Date:** 2026-07-10
**Status:** Approved
**Parent project:** Enterprise Fleet Management System (FMS)
**Phase:** 1 — sub-phase 1b of 3 (builds on 1a Foundation)

## Scope

The generic, reusable engines every later module depends on:

1. **Document Type Maintenance** — per-document configuration flags.
2. **Generic Auto Numbering Engine** — configurable, concurrency-safe document numbering.
3. **Approval Matrix / Path Maintenance + Approval Engine runtime** — dynamic approval
   resolution per master prompt chain: Document Type → Amount Range → Approval Matrix →
   Approval Path → Approval Levels, unlimited levels, actions Submit/Approve/Reject/Return/Cancel.

## Data Model

- **DocumentType**: code (unique, e.g. TT/MO/PR/ATD), name, description,
  requires_approval, auto_numbering, printable, mobile_available, attachment_allowed.
- **NumberingScheme**: document_type_id (FK, unique), prefix, suffix, include_year,
  include_month, digit_count (default 6), separator (default '-'),
  reset_policy ENUM-as-string: NEVER | YEARLY | MONTHLY.
- **NumberingCounter**: scheme_id (FK), year (0 when not applicable), month (0),
  last_number. Unique (scheme_id, year, month). Increment under row lock
  (`with_for_update`) for concurrency safety.
- **ApprovalPath**: name, description.
- **ApprovalLevel**: path_id (FK), level_number (1..N), approver_type ROLE|USER,
  role_id (nullable FK), user_id (nullable FK). Unique (path_id, level_number).
- **ApprovalMatrix**: document_type_id (FK), approval_path_id (FK),
  min_amount / max_amount (nullable Numeric(18,2); both NULL = amount-independent),
  effective_from / effective_to (nullable dates). Overlap validation in service.
- **ApprovalInstance**: document_type_id, reference_table, reference_id,
  amount (nullable), current_level, status DRAFT|PENDING|APPROVED|REJECTED|RETURNED|CANCELLED,
  approval_path_id (resolved at submit).
- **ApprovalAction**: instance_id, level_number, action SUBMIT|APPROVE|REJECT|RETURN|CANCEL,
  acted_by (user id), remarks, acted_at.

All models on BaseModel (audit columns, soft delete) — automatic audit trail from 1a applies.

## Services

- **AutoNumberingService.generate(document_type_code) -> str**
  Format: [prefix][sep][YYYY][sep][MM][sep][NNNNNN][sep][suffix] — segments included
  per scheme flags. Counter row selected FOR UPDATE, scoped by reset policy
  (NEVER → (0,0); YEARLY → (year,0); MONTHLY → (year,month)). Flushes; caller commits.
  `preview(scheme) -> str` renders a sample without consuming a number (UI live preview).
- **ApprovalEngine**
  - `submit(document_type_code, reference_table, reference_id, amount=None, user)` —
    resolves matrix (doc type + amount in [min,max], NULL-bounds treated as open,
    effective-date filtered), creates instance at level 1 PENDING, records SUBMIT.
    If document type has requires_approval=False → instance goes straight to APPROVED.
  - `approve(instance, user, remarks)` — validates user is an eligible approver for
    current level (holds level's role, or is level's user); advances level or sets
    APPROVED at last level.
  - `reject(instance, user, remarks)` — eligible approver only; status REJECTED (terminal).
  - `return_document(instance, user, remarks)` — eligible approver; status RETURNED,
    current_level reset; document owner may resubmit (new SUBMIT restarts at level 1).
  - `cancel(instance, user, remarks)` — submitter (or user.cancel-any permission later);
    status CANCELLED (terminal). Not allowed once APPROVED.
  - Every state change records an ApprovalAction. Event hook `on_event(callback)`
    (simple in-process registry) — Notification Engine (1c) subscribes here.
  - Errors: NoMatrixError, NotEligibleApproverError, InvalidStateError.
- **DocumentTypeService / NumberingSchemeService / ApprovalPathService /
  ApprovalMatrixService** — CRUD with rules: unique doc type code; one scheme per doc
  type; path must have ≥1 level, contiguous level numbers from 1; matrix ranges for the
  same doc type must not overlap (date+amount).

## UI (System Administration section additions)

- Document Types: list + form (flag checkboxes).
- Numbering Schemes: list + form with live JS preview of the generated format.
- Approval Paths: list + form with dynamic level rows (add/remove, role-or-user select).
- Approval Matrix: list + form (doc type, amount range, path, effective dates);
  overlap errors surfaced via flash.
All using 1a shell, DataTables, Select2, SweetAlert2, @require_permission, breadcrumbs.

Permissions registered: doctype.view/create/update/delete, numbering.view/create/update/delete,
approvalpath.view/create/update/delete, approvalmatrix.view/create/update/delete.

## Testing

Unit: numbering formats (all flag permutations), yearly/monthly/never reset behavior,
sequential increments; matrix resolution incl. open bounds, amount-independent, no-match
error, overlap validation; approval walk multi-level approve→APPROVED, reject, return +
resubmit, cancel rules, eligibility (role-based and user-based), requires_approval=False
short-circuit. Integration: CRUD screens render + permission 403s; end-to-end submit/approve
via engine with role approvers.

## Out of Scope (1b)

Notification delivery (1c), attachments, printing/PDF, mobile endpoints, System Parameters.

## Notes / Risks

- Numeric(18,2) for amounts — portable MySQL/MSSQL.
- Approver eligibility is role-or-user per level (architect recommendation, accepted).
- In-process event hooks only; async delivery arrives with Celery-backed notifications in 1c.
