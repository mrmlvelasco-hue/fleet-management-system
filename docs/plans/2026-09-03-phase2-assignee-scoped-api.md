# Phase 2 — Assignee-Scoped API — Implementation Plan

**Goal:** Make `AssigneeScopeService` actually guard something, and close J1–J4 for the mobile
audience.

**Spec:** `docs/specs/2026-09-03-phase2-assignee-scoped-api-design.md`

### Task 1: Close J3 — checklist detail IDOR
Files: `app/modules/transactions/vehicle_checklist/service.py`, `app/modules/api/checklists.py`,
`tests/unit/test_checklist_detail_scope.py` (create)

- [ ] Failing tests: GET/PUT `/checklists/<id>` → 404 for another user's checklist when the caller
      lacks `checklist.submit`; a caller WITH `checklist.submit` still sees everything.
- [ ] Implement `get_visible(cid, user)` mirroring `list_checklists`; route both endpoints through it.
- [ ] Mutation-check: drop the scope filter — the IDOR test must fail. Commit only if caught.

### Task 2: `/api/v1/my/vehicles` + the guard helper
Files: `app/modules/api/my_vehicles.py` (create), `app/modules/api/routes.py` (register),
`tests/unit/test_my_vehicles_scope.py` (create)

- [ ] Failing tests: list returns only the caller's; `[]` when unlinked; detail 404 for a
      branch-mate's vehicle even under org scope; closed assignment grants nothing.
- [ ] Implement `_assigned_vehicle_or_404` and the two endpoints.
- [ ] Mutation-check the helper: make it fall back to `VehicleService.get_visible()`. Tests must fail.

### Task 3: Odometer and documents (J2, J4 for the app)
- [ ] Failing tests: odometer 404 + reading NOT written for an unheld vehicle; document list and
      download 404 for an unheld vehicle.
- [ ] Implement, reusing the helper.

### Task 4: `/my/atds` — caller is the named driver
- [ ] Failing test: an ATD on the caller's own vehicle issued to a DIFFERENT driver is excluded.
- [ ] Implement.

### Task 5: The seed guard
- [ ] Test asserting the field-user role does not hold `vehicle.view`, so the next person tidying
      seed data cannot silently reopen J1 for assignees.

### Task 6: Regression + bundle v231 + apply guide
