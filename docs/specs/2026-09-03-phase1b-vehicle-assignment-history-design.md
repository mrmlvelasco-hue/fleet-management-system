# Phase 1b — Vehicle Assignment History & Assignee Scope — Design Spec

**Date:** 2026-09-03
**Status:** Approved
**Parent:** FMS Mobile Application + APK Release Management
**Phase:** 1 of 7, sub-phase **1b** of 2 (1a Mobile access & assignee link → 1b this spec)

## Context

Phase 1a established *who* a login is (`drivers.user_id`) and *whether* their phone may sign in
(`users.mobile_access`). 1b establishes *what they are assigned to*, which is the last thing the
backend needs before any assignee-scoped endpoint can exist.

Today `Vehicle.assigned_driver_id` is a single nullable FK. It records the present and nothing else:
no start date, no end date, no history, and no record of which document caused the change. Three
separate workflows write it — a manual edit on the Vehicle form, ATD final approval, and completion
of an Operational Maintenance Order (the Vehicle Assignment Memo) — and all three discard everything
except the resulting driver id.

## Decisions Made

| Decision | Choice | Why not the alternative |
|---|---|---|
| Source of truth | `vehicle_assignments` is the record; `Vehicle.assigned_driver_id` remains a **real column**, maintained as a cache of the open row | Making it a Python property would break the three places that filter it in **SQL** (`Vehicle.query.filter_by(assigned_driver_id=…)` in `api/drivers.py` and twice in `master_data/routes.py`). A property is invisible to SQL, so those queries would not error — they would silently return nothing, and the Assignee screen would quietly stop showing vehicles. A silent wrong answer is worse than a loud break. |
| Assignments per vehicle | At most **one open** row per vehicle | Concurrent assignments would make "who holds this vehicle" ambiguous, which is the question the table exists to answer. |
| Vehicles per assignee | **Several allowed** | Already true in practice — `_assigned_vehicles()` returns a list and nothing prevents it. Confirmed as intended, so Phase 2's endpoint is `/api/v1/my/vehicles`, plural. |
| Provenance | Record `source` and the originating document | The hooks already know whether an ATD or an MO caused each change and currently throw it away. Keeping it makes the table answer "why did this vehicle change hands on the 14th", which is what an audit actually asks. |
| Backfill start date | `NULL`, `source='BACKFILL'` | A null start honestly says "in force, start unknown". Inventing a plausible date would put fiction into an audit table. |

## Data Model

```
vehicle_assignments
  id             PK
  vehicle_id     FK vehicles.id   NOT NULL, indexed
  driver_id      FK drivers.id    NOT NULL, indexed
  assigned_from  DATE     NULL     -- NULL only for backfilled rows
  assigned_to    DATE     NULL     -- NULL = the open (current) assignment
  source         VARCHAR(20) NOT NULL   -- MANUAL | ATD | MO | BACKFILL
  source_table   VARCHAR(50) NULL       -- e.g. 'authority_to_drives'
  source_id      INTEGER     NULL       -- the originating document's id
  remarks        TEXT     NULL
  + BaseModel audit columns
```

**The one-open-row invariant is enforced in the service, not by a database constraint.** A partial
unique index (`UNIQUE … WHERE assigned_to IS NULL`) is the natural expression and is not portable —
MySQL has no filtered indexes, and the master prompt requires the schema stay migratable to SQL
Server. Enforcing it in one service method that every writer funnels through is the honest version;
a constraint that only exists on one engine would give false confidence on the others.

## Components

### `VehicleAssignmentService`

The single writer. Every path that changes who holds a vehicle goes through it.

| Method | Behaviour |
|---|---|
| `assign(vehicle_id, driver_id, source, source_table, source_id, assigned_from, remarks, user)` | Closes any open row (`assigned_to` = today), opens a new one, updates `Vehicle.assigned_driver_id` — one transaction |
| `release(vehicle_id, source, user)` | Closes the open row, sets the column to `None` |
| `current_for_vehicle(vehicle_id)` | The open row, or `None` |
| `history_for_vehicle(vehicle_id)` | All rows, newest first |
| `current_for_driver(driver_id)` | Open rows — a list, since one person may hold several |

`assign()` is **idempotent**: re-assigning a vehicle to the driver who already holds it returns the
existing row untouched. Without this, saving the Vehicle form twice would close and reopen an
assignment, producing a history of events that never happened. An audit table that records the act
of pressing Save is worse than no audit table.

### `AssigneeScopeService`

Read-only. The thing Phase 2 will scope its endpoints on. Built and tested here, **wired to no
endpoint yet** — that is Phase 2, deliberately, so the scope logic can be proven before anything
depends on it.

| Method | Returns |
|---|---|
| `assignee_for(user)` | The `Driver` linked to this login, or `None` |
| `assigned_vehicle_ids(user)` | Vehicle ids from this user's open assignments — `[]` when unlinked |
| `covers_vehicle(user, vehicle_id)` | `True` only if that vehicle is currently assigned to this user |

`covers_vehicle` returns `False` for an unlinked user, an inactive user, and an assignment that has
been closed. It is deliberately **not** org-scope aware: it answers a narrower question than
`UserOrgScopeService.covers()` and must never widen into it, because widening is exactly the defect
(finding J1) that Phase 2 exists to fix.

### Funnelling the three writers

| Writer | Change |
|---|---|
| `assignment_hooks.assign_driver_to_vehicle()` | Delegates to the service. Already the shared entry point for ATD approval and MO completion, so two of the three writers are one already. Gains `source` and the document reference its callers already know. |
| `VehicleService.assign_driver()` | Delegates, `source='MANUAL'` |
| Vehicle form create/edit (`master_data/routes.py`) | Reconciles through the service after save, rather than setting the column through `**kwargs` |

## Migration & Backfill

1. Create `vehicle_assignments`.
2. Insert one open row per vehicle where `assigned_driver_id IS NOT NULL`: `assigned_from` NULL,
   `assigned_to` NULL, `source='BACKFILL'`.

`Vehicle.assigned_driver_id` is **not** dropped, altered, or renamed. Nothing that reads it today
changes behaviour.

## Testing

**Unit**
- `assign` opens a row and updates the column
- `assign` over an existing assignment closes the old row and leaves exactly one open
- `assign` to the same driver is a no-op — no second row, no churn
- `release` closes the row and nulls the column
- History survives reassignment (the point of the table)
- `source` and document reference are recorded
- One vehicle, one open row — asserted after a chain of reassignments
- One driver may hold several vehicles concurrently
- `AssigneeScopeService`: unlinked → `[]`; linked → their vehicles; closed assignment excluded;
  `covers_vehicle` false for a vehicle assigned to someone else

**Guard (mutation-checked)**
- `covers_vehicle` must not fall back to org scope. Asserted with a user whose org scope covers a
  branch containing a vehicle assigned to a *different* person — the answer must still be `False`.

**Integration**
- ATD final approval writes an assignment row with `source='ATD'` and the ATD's id
- MO completion writes one with `source='MO'`
- Vehicle form edit writes one with `source='MANUAL'`
- Existing reads unchanged: assignee detail still lists vehicles, vehicle list still shows assignee

## Out of Scope for 1b

- `/api/v1/my/*` endpoints and closing J1–J4 (→ Phase 2)
- Any UI for assignment history (→ later; the data is captured now so the screen has something to
  show when it is built)
- Mobile screens, APK release management (→ Phases 3–5)

## Open Risks

- **The reconcile-after-save path on the Vehicle form is the fiddliest part.** It must run after the
  record exists and must no-op when the driver has not changed, or every unrelated edit to a vehicle
  writes a spurious assignment event.
- Backfilled rows carry a NULL `assigned_from`. Any future report grouping by start date must treat
  NULL as "predates the system" rather than dropping the row.
- MySQL `DATE` vs SQLite text dates: the suite runs on SQLite. Verify the backfill and the
  `assigned_to IS NULL` predicate against MySQL 8.4 before delivery.
