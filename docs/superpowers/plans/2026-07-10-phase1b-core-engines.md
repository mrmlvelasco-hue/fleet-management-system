# Phase 1b — Core Engines Implementation Plan

> Executed inline (same workflow as 1a). TDD per task, commit per task.
> Spec: docs/superpowers/specs/2026-07-10-phase1b-core-engines-design.md

Conventions: all 1a rails (BaseModel, BaseRepository, services commit, thin routes,
@require_permission, registry-registered permissions, templates on 1a shell).

New module package: `app/modules/document_config/` (DocumentType + NumberingScheme
maintenance) and `app/modules/approval_config/` (Paths + Matrix maintenance).
Engines live in core: `app/core/numbering/` and `app/core/approval/`.

### Task 1: DocumentType model + service + repo
- [ ] models: DocumentType (code unique + flags)
- [ ] tests: create, duplicate code rejected, flags default sensible (requires_approval True? -> default False; explicit)
- [ ] commit

### Task 2: NumberingScheme + NumberingCounter models, AutoNumberingService
- [ ] models with unique constraints
- [ ] service generate(): format assembly per flags, counter row with_for_update,
      reset policy scoping, preview()
- [ ] tests: TT-2026-000001 style output, no-year, month, suffix, digit widths,
      sequential increments, yearly reset (monkeypatch date), monthly reset, never
- [ ] commit

### Task 3: ApprovalPath + ApprovalLevel models + path service
- [ ] models; service validates ≥1 level and contiguous numbering from 1
- [ ] tests: valid path, empty rejected, gap rejected
- [ ] commit

### Task 4: ApprovalMatrix model + matrix service + resolution
- [ ] model; service create/update with overlap validation
- [ ] resolve(document_type, amount, on_date) with open bounds + NULL-amount matrices
- [ ] tests: in-range, open-min, open-max, amount-independent, no-match error, overlap rejected
- [ ] commit

### Task 5: ApprovalInstance + ApprovalAction models + ApprovalEngine
- [ ] engine submit/approve/reject/return_document/cancel + eligibility + events
- [ ] tests: 2-level walk, requires_approval=False shortcut, reject terminal,
      return + resubmit, cancel rules, role and user eligibility, wrong user blocked,
      event hook fires
- [ ] commit

### Task 6: Document config UI (Document Types + Numbering Schemes)
- [ ] routes/forms/templates + permissions registered + sidebar entries
- [ ] integration tests: 403 without perm, 200 with, create via POST
- [ ] commit

### Task 7: Approval config UI (Paths + Matrix)
- [ ] routes/forms/templates (dynamic level rows JS), overlap error flash
- [ ] integration tests: CRUD + permission enforcement
- [ ] commit

### Task 8: Migration, README update, package zip
- [ ] flask db migrate/upgrade clean
- [ ] full suite green; zip to outputs
- [ ] commit
