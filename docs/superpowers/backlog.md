# Enhancement Backlog (Deferred — post-Phase-8 or as noted)

Enhancements identified during testing/review that are intentionally deferred so
core phased delivery isn't interrupted. Each entry notes what already exists
today vs. what's still needed.

---

## 1. Custom Fields Engine
**Raised:** Phase 2 (vehicle master feedback)
**Priority:** Medium
**What's needed:** Admin-configurable custom fields per master/module (JSON
column + Custom Field Definition config table) so new fields can be added
without schema/code changes. See design discussion in chat history
(2026-07-11) for the recommended approach (JSON column on BaseModel +
`CustomFieldDefinition` config + dynamic form rendering + report column
injection).
**Status:** Not started.

## 2. Dashboard Widget — "My Actions"
**Raised:** Phase 3a review
**Priority:** Medium-High
**What's needed:** A dashboard widget showing, per logged-in user:
- Pending approvals awaiting their action (query ApprovalInstance where
  current user is eligible approver at current_level)
- Draft transactions they created (query each transaction model where
  requested_by = user, status/approval_instance is None or DRAFT)
- Ongoing transactions in process (PENDING approval instances they submitted)
- Recently returned/rejected requests needing their attention
- Status summary counts (submitted/approved/rejected/pending this period)
**What's needed to build:** A cross-module query service that already has
the pieces (ApprovalInstance, per-module models with requested_by) — mainly
needs a `MyActionsService` aggregating across all transaction modules
(growing list as 3b/3c land) and a dashboard widget UI. Natural fit for
Phase 4 (Dashboard) since that's when real KPI wiring happens anyway.
**Status:** Not started. Recommend building as part of Phase 4.

## 3. Transaction Commenting / Remarks Feature
**Raised:** Phase 3a review
**Priority:** Medium-High
**What's needed:** A generic `Comment` model (reference_table, reference_id,
user_id, body, created_at) — same reference_table/reference_id pattern as
Attachment and AuditLog — so any transaction gets a comment thread with zero
per-module code, mirroring how Attachments work today. Approval remarks
(already captured on ApprovalAction.remarks) could be surfaced in the same
timeline UI for a unified discussion + decision history view.
**What already exists:** ApprovalAction.remarks captures approve/reject/
return remarks already (just not currently shown in a unified comment-style
UI). AuditLog already captures every field change.
**Status:** Not started. Good candidate for a small cross-cutting addition
early in Phase 3b/3c since every new transaction module benefits immediately
(same leverage pattern as Attachments).

## 4. Supporting Document Attachment — version history
**Raised:** Phase 3a review
**Priority:** Medium (attachments themselves already work)
**What already exists:** Multi-file attachment upload/view/download already
works generically on every master (Phase 2) and now every transaction module
(Phase 3a) via AttachmentService + the shared attachment panel. Images
preview inline; all files audit-logged automatically.
**What's needed:** Version history when a document is re-uploaded to replace
an existing one (currently a re-upload just adds a new attachment row,
functionally fine but not explicitly linked as "v2 of document X"). Would
need a `replaces_attachment_id` self-referencing FK and a version-chain UI.
**Status:** Not started — low urgency since multi-upload already covers the
practical need (old + new file both visible); formal versioning can wait.

---

## Suggested sequencing
Items 2 and 3 have real technical leverage the sooner they land (every
future transaction module gets them "for free," same as Attachments did) —
worth considering pulling item 3 (Comments) forward into Phase 3b as a small
cross-cutting addition, and item 2 (My Actions widget) into Phase 4 where
dashboard KPI wiring happens anyway. Items 1 and 4 are fine as strict
post-Phase-8 polish.
