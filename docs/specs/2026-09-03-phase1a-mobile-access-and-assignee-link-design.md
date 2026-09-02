# Phase 1a — Mobile Access Flag & Assignee ↔ System Account Link — Design Spec

**Date:** 2026-09-03
**Status:** Approved
**Parent:** FMS Mobile Application + APK Release Management
**Phase:** 1 of 7, sub-phase **1a** of 2 (1a this spec → 1b Assignment history & scope service)

## Context

The mobile analysis report (`FMS_MOBILE_APK_ANALYSIS_AND_CLIENT_BRIEF.md`, §D, §M1) established that
the backend cannot answer *"which vehicle belongs to the person holding this token"*, because nothing
joins a login to an assignee. `User.employee_id` is free text; `Driver` has no `user_id`.

Two client decisions were taken on 2026-09-03:

1. Vehicle assignees who need the phone become ordinary FMS users under User Maintenance, and the
   system must record **explicitly** whether a given user has mobile access.
2. The Assignee list must show, and the Assignee form must set, the linked system account.

## Decisions Made

| Decision | Choice | Rejected alternatives |
|---|---|---|
| How mobile access is identified | Explicit `users.mobile_access` boolean | **Role-based** (`mobile.access` permission): mobile access is a *channel*, not a capability. A Fleet Manager may hold every checklist permission and never touch a phone; a driver has mobile access with almost no permissions. Folding a channel into the permission matrix means every future role edit silently grants or revokes phone access. **Derived from the assignee link**: the field app already ships the Approval Inbox, and an approver on a phone is not an assignee; deriving also removes the ability to suspend a lost phone without unlinking the person from their vehicle. |
| Where the flag is enforced | `POST /api/v1/auth/token` and `POST /api/v1/auth/refresh`, on the native branch only | Per-endpoint checks: 30+ places to keep in sync, and each one a chance to forget. |
| Assignee ↔ account link | `drivers.user_id`, nullable, unique FK to `users.id` | A link table: the relationship is 1:1 and carries no attributes of its own, so a table would add a join and a second write path for nothing. `employee_id`/`employee_number` string matching: silent, unenforced, and breaks the moment someone retypes an employee number. |
| Link vs. access | Two independent facts | Collapsing them: see above. |
| Phase split | 1a additive only; `vehicle_assignments` history deferred to 1b | One pass: replacing `Vehicle.assigned_driver_id` touches code across the system, has a different risk profile, and would make this change impossible to review or revert on its own. |

## Data Model Changes

```
users
  + mobile_access   BOOLEAN NOT NULL DEFAULT 0

drivers
  + user_id         INTEGER NULL, FK users.id, UNIQUE
```

Both are additive. Nothing existing reads either column, so an install that runs the migration and
changes nothing else behaves exactly as it did before — with one deliberate exception, stated in
§Deployment Note below.

`drivers.user_id` is **nullable** because most assignees will never have a login, and **unique**
because one system account must not be two different assignees — an ambiguity that would make
"which vehicle belongs to this token" unanswerable again in 1b, which is the entire point of the link.

## Components

### `users.mobile_access` — the channel gate

Enforced in exactly one place: the native branch of the auth endpoints.

```
POST /api/v1/auth/token   {"client": "native"}  and not user.mobile_access
    → 403 {"error": "mobile_access_denied"}
POST /api/v1/auth/refresh  refresh_token in BODY  and not user.mobile_access
    → 401
```

Three properties follow from putting it there and nowhere else:

- **The web is untouched.** A browser never sends `client: "native"`, so `mobile_access` has no
  effect on the React app or the Jinja UI. It is not a second permission system.
- **Permissions still decide everything else.** The flag governs whether a phone can obtain a token
  at all. What the holder may then *do* is unchanged: the same permission codes, the same roles.
- **Revocation is real.** The refresh check is what makes it a kill switch. Without it, clearing the
  flag would leave a valid 14-day refresh token on the device, still minting access tokens.

**Known limitation, consistent with existing behaviour.** An access token already issued stays valid
for its remaining lifetime (≤12 hours). This matches `/auth/logout`, which likewise ends the ability
to mint new tokens rather than revoking outstanding ones. Genuine immediate revocation needs the
`jti` denylist recorded as finding J5, and is out of scope here.

### `drivers.user_id` — the link

Owned by `DriverService`, which enforces:

| Rule | Behaviour |
|---|---|
| One account, one assignee | Linking a user already linked to another assignee raises `DuplicateAssigneeLinkError` |
| Link ≠ access | Linking does **not** set `mobile_access`; the two are set independently |
| Unlink is safe | Clearing the link leaves the user account entirely untouched |
| Inactive users | An inactive user cannot be linked (`InvalidAssigneeError`) |

### API surface

No new endpoints. Three payload additions:

- `GET /api/v1/me` gains `mobile_access` (bool) and `assignee` — `{id, person_id, employee_number,
  full_name}` or `null`. This is what the mobile client will read in 1b to find its own vehicle, and
  what lets the app show a coherent "your account is not set up for the field app" message instead of
  a bare 403.
- Driver list rows gain `user_id` and `username`.
- Driver detail gains the same.

### UI

| Screen | Change |
|---|---|
| `user_form.html` | "Mobile access" checkbox, with help text stating it controls the field app only |
| `users_list.html` | "Mobile" column — badge when enabled, dash when not |
| `driver_form.html` | "System Account" searchable single-select (Select2), optional, blank = unlinked |
| `driver_list.html` | "System Account" column — username, or a dash |

## Testing

**Unit**

- `DriverService.link_user` sets the link; `link_user(None)` clears it
- Linking an account already linked to another assignee raises `DuplicateAssigneeLinkError`
- Linking an inactive user raises `InvalidAssigneeError`
- Linking does not alter `user.mobile_access`
- `UserService.create_user` / `update_user` persist `mobile_access`
- `GET /api/v1/me` returns `mobile_access` and the `assignee` block (linked and unlinked cases)

**Security (the guards that matter — these get mutation-checked, per the attachment-bypass lesson)**

- `POST /auth/token` with `client: "native"` and `mobile_access=False` → 403
- The same credentials **without** `client: "native"` → 200 (web unaffected)
- `POST /auth/refresh` with the token in the body and `mobile_access=False` → 401
- `POST /auth/refresh` via cookie is unaffected by the flag

**Integration**

- User form round-trip persists `mobile_access`
- Driver form round-trip persists and clears `user_id`
- Driver list renders the username for a linked assignee

## Deployment Note — read before applying

`mobile_access` defaults to **false**, which is the correct secure default and means **no account can
obtain a native token until an administrator ticks the box**. On an install with a field APK already
in use, every phone stops logging in at the moment the migration runs until the flag is set on those
users. This is intended and must be scheduled, not discovered.

## Out of Scope for 1a

- `vehicle_assignments` history table and `AssigneeScopeService` (→ 1b)
- `/api/v1/my/*` assignee-scoped namespace (→ Phase 2)
- Closing findings J1–J4 (→ Phase 2)
- `jti` denylist for J5 (→ Phase 6)
- Mobile screens, APK release management (→ Phases 3–5)
- Backfilling links for existing drivers — a data exercise for the client, not a migration

## Open Risks

- MySQL enforces the `UNIQUE` constraint on `drivers.user_id` across NULLs differently from nothing —
  multiple NULLs are permitted in both MySQL and SQLite, which is the behaviour relied on here.
  Verify against MySQL 8.4 before delivery; the SQLite suite will not catch a divergence.
- Adding a nullable column with a default to `users` and `drivers` on a large production table locks
  briefly. Both tables are small (hundreds of rows) at this client, so no online-DDL strategy is
  proposed.
