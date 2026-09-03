# Phase 2 — Assignee-Scoped API: Closing J1–J4 — Design Spec

**Date:** 2026-09-03
**Status:** Approved
**Parent:** FMS Mobile Application + APK Release Management
**Phase:** 2 of 7

## Context

Phase 1 gave the backend three facts it previously could not express: who a login is
(`drivers.user_id`), whether their phone may sign in (`users.mobile_access`), and what they are
assigned to (`vehicle_assignments`). `AssigneeScopeService` exists and is tested, and guards nothing.

Phase 2 is where it starts guarding. Four findings from the mobile analysis report are open:

| Finding | Today |
|---|---|
| **J1** | `VehicleService.get_visible()` scopes on **org**, so a driver with `vehicle.view` reads every vehicle in their branch |
| **J2** | `POST /vehicles/by-plate/odometer` matches the plate against the same org-scoped list, so a driver can post a reading against any branch-mate's vehicle |
| **J3** | `GET`/`PUT /checklists/<id>` call a bare `db.session.get()` with **no scope check at all** — a driver who can no longer *list* another's inspection can still fetch and edit it by guessing the sequential id |
| **J4** | Attachment download is scoped by parent visibility, which is org scope again — so any account with `vehicle.view` can download the OR and CR of any vehicle in its branch |

Until these close, driver-facing screens must not reach real users: they would work perfectly and
expose the whole branch.

## Decisions Made

| Decision | Choice | Why not the alternative |
|---|---|---|
| Where assignee scope is enforced | A new `/api/v1/my/*` namespace | Retrofitting scope onto `/api/v1/vehicles/*` would break the web app, the telematics feed and external integrations, all of which legitimately operate on org scope. A conditional branch inside those endpoints would put two authorisation models in one function — the thing that later gets edited wrongly. One scope rule per endpoint, readable at a glance in review. |
| Response for a vehicle you don't hold | **404**, not 403 | Inside `/my/*`, a vehicle you are not assigned is not merely forbidden — it is not part of your world. A 403 confirms the record exists, turning sequential ids and plate numbers into an enumeration oracle. This differs deliberately from the rest of the API, where 403 is right because the caller legitimately knows the resource exists. |
| ATD visibility | Only ATDs where the caller **is the named driver** | Client decision, 2026-09-03: the ATD in the app is for the assignee themselves and nothing else. Note this is stricter than "ATDs for my assigned vehicle" — a vehicle's ATD issued to somebody else is not visible even while the caller holds that vehicle. |
| J3 fix | Detail and update inherit the **list's** rule | The list already scopes to `created_by` for anyone lacking `checklist.submit`. Detail should not have a different rule from the list that produced the link; the defect is precisely that it had none. |
| J1/J2/J4 approach | Fixed by not being used — the field app calls `/my/*` | The existing endpoints keep org scope, which is correct for their callers. J1/J2/J4 are closed *for the mobile audience*; see "Honest scope" below. |

## Honest scope of the fix

J1, J2 and J4 describe org-scoped endpoints. Phase 2 does **not** narrow those endpoints — it gives
the field app a namespace that never touches them. The finding is closed for the Vehicle Assignee
audience, which is the audience that created the risk.

What remains true afterwards: a user who holds `vehicle.view` and knows the URLs can still call
`/api/v1/vehicles/*` directly and see their branch. **The mitigation is that assignees are granted
only the permissions the field app needs.** Phase 2 therefore also adds a test asserting that the
seeded field-user role does not hold `vehicle.view` — a scope decision expressed as a permission
grant needs a guard, or the next person to tidy the seed data silently reopens the finding.

J3 is different: it is an outright missing check on an endpoint the mobile app uses, and it is fixed
in place.

## Components

### `app/modules/api/my_vehicles.py` — the assignee namespace

| Endpoint | Returns |
|---|---|
| `GET /api/v1/my/vehicles` | The caller's currently-assigned vehicles. `[]` when unlinked — never an error |
| `GET /api/v1/my/vehicles/<id>` | Detail, 404 unless currently assigned to the caller |
| `POST /api/v1/my/vehicles/<id>/odometer` | Reading for a vehicle the caller holds (closes J2 for the app) |
| `GET /api/v1/my/vehicles/<id>/documents` | OR / CR attachments for that vehicle (closes J4 for the app) |
| `GET /api/v1/my/vehicles/<id>/documents/<aid>/download` | The file, re-checking assignment |
| `GET /api/v1/my/atds` | ATDs where the caller is the named driver |
| `GET /api/v1/my/atds/<id>` | Detail, 404 unless the caller is the named driver |

Every one resolves scope through `AssigneeScopeService`. None calls `VehicleService.get_visible()`.

**The guard is a single helper**, `_assigned_vehicle_or_404(vehicle_id, api_user)`, used by every
vehicle-bound endpoint. One function to read in review, one function to mutation-test, and no
endpoint that can quietly forget to call it — a scope check copied inline seven times is seven
chances to omit the eighth.

Permissions still apply on top. The namespace narrows *which records*; the permission codes decide
*which actions*. `mobile_access` remains a separate channel gate. Three independent questions, three
independent mechanisms.

### J3 — `VehicleChecklistService.get_visible(cid, user)`

Mirrors `list_checklists`: a caller lacking `checklist.submit` sees only checklists they created.
`GET` and `PUT /checklists/<id>` both route through it and return 404 (not 403) on a miss, for the
same enumeration reason as above.

## Testing

**Guards (mutation-checked — these are the point of the phase)**

- `/my/vehicles` returns only the caller's vehicles, with a same-branch vehicle held by someone else
  present in the database and absent from the response
- `/my/vehicles/<id>` → 404 for a branch-mate's vehicle, **even when the caller's org scope covers
  that branch**
- Odometer POST → 404 for a vehicle the caller does not hold; the reading is not written
- Document list and download → 404 for a vehicle the caller does not hold
- A released (closed) assignment grants nothing — a vehicle handed back last month is gone
- `/my/atds` excludes an ATD for the caller's own vehicle issued to a different driver
- `GET`/`PUT /checklists/<id>` → 404 for a checklist created by someone else, by a caller without
  `checklist.submit` (J3)
- A caller *with* `checklist.submit` still sees all checklists — the reviewer workflow must not break

**Regression**

- The existing `/api/v1/vehicles/*` endpoints are unchanged and still org-scoped
- The Fleet Officer web workflows are untouched

## Out of Scope for Phase 2

- Mobile screens consuming these endpoints (→ Phase 3)
- APK release management (→ Phases 4–5)
- The `jti` refresh-token denylist, finding J5 (→ Phase 6)
- Narrowing `/api/v1/vehicles/*` itself — see "Honest scope"

## Open Risks

- **The seeded field-user role is now load-bearing.** Scope for the mobile audience depends partly on
  assignees not holding `vehicle.view`. The guard test makes that explicit rather than tacit.
- 404-instead-of-403 will look like a bug to anyone debugging without reading this document. The
  helper carries a comment saying why.
- `AssigneeScopeService.assigned_vehicle_ids` runs per request. At this client's scale (hundreds of
  vehicles, a handful of assignments per user) an indexed lookup is ample; revisit only if the
  assignment table grows unexpectedly.
