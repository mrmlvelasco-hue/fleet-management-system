# Phase 1b — Vehicle Assignment History & Assignee Scope — Implementation Plan

**Goal:** Make assignment a dated, sourced history rather than a single overwritten column, and
provide the scope service Phase 2 will use to close findings J1–J4.

**Architecture:** `vehicle_assignments` becomes the record. `Vehicle.assigned_driver_id` stays a real
column, maintained as a cache of the open row, so every existing SQL filter and template read keeps
working. All three writers funnel through `VehicleAssignmentService`.

**Spec:** `docs/specs/2026-09-03-phase1b-vehicle-assignment-history-design.md`

---

### Task 1: Model + migration + backfill
Files: `app/modules/master_data/vehicle/assignment_models.py` (create),
`migrations/versions/c2b8d1e04f32_add_vehicle_assignments.py` (create),
`tests/unit/test_vehicle_assignment_model.py` (create)

- [ ] Failing test: a row can be created; `assigned_to` NULL means open.
- [ ] Run — expect ImportError.
- [ ] Implement `VehicleAssignment` with the columns from the spec, `down_revision='b1a7c0d93e21'`.
- [ ] Migration backfills one open row per vehicle with `assigned_driver_id IS NOT NULL`,
      `source='BACKFILL'`, `assigned_from` NULL.
- [ ] Run — PASS. Verify single Alembic head. Commit.

### Task 2: VehicleAssignmentService
Files: `app/modules/master_data/vehicle/assignment_service.py` (create),
`tests/unit/test_vehicle_assignment_service.py` (create)

- [ ] Failing tests: assign opens + syncs column; reassign closes the old row leaving exactly one
      open; assign to the same driver is a no-op; release closes and nulls; history survives;
      source and document id recorded; one driver may hold two vehicles.
- [ ] Run — expect ImportError.
- [ ] Implement `assign`, `release`, `current_for_vehicle`, `history_for_vehicle`,
      `current_for_driver`.
- [ ] Run — PASS. Commit.

### Task 3: AssigneeScopeService
Files: `app/modules/user_management/assignee_scope_service.py` (create),
`tests/unit/test_assignee_scope_service.py` (create)

- [ ] Failing tests: unlinked user → `[]`; linked → their vehicle ids; closed assignment excluded;
      `covers_vehicle` False for someone else's vehicle; **False even when org scope covers that
      branch**.
- [ ] Run — expect ImportError.
- [ ] Implement `assignee_for`, `assigned_vehicle_ids`, `covers_vehicle`.
- [ ] Run — PASS.
- [ ] Mutation-check: make `covers_vehicle` fall back to `UserOrgScopeService().covers()`. The
      org-scope test must fail. Revert. Commit only after it was caught.

### Task 4: Funnel the three writers
Files: `app/modules/master_data/vehicle/assignment_hooks.py`,
`app/modules/master_data/vehicle/service.py`, `app/modules/master_data/routes.py`,
`tests/integration/test_assignment_sources.py` (create)

- [ ] Failing tests: ATD final approval writes `source='ATD'` with the ATD id; MO completion writes
      `source='MO'`; Vehicle form edit writes `source='MANUAL'`; an unrelated edit to a vehicle
      writes NO new row.
- [ ] Run — expect no rows written.
- [ ] Implement: hooks and `VehicleService.assign_driver` delegate; the Vehicle form reconciles
      through the service after save.
- [ ] Run — PASS. Commit.

### Task 5: Regression + bundle
- [ ] Batched pytest across the vehicle, driver, ATD, MO and API suites.
- [ ] `git bundle create fms_fixes_v230.bundle HEAD main --tags`
- [ ] `APPLY_GUIDE_v230.md` — note the backfill and that no existing read changes.
