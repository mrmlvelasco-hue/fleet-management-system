# Parity Audit — Phase 1a Fields in React

**Date:** 2026-09-03
**Flask source of truth:**
`user_form.html`, `users_list.html`, `driver_form.html`, `driver_list.html`, `driver_detail.html`
**React targets:** `AdminRbac.tsx` (`UsersPage`), `DriverForm.tsx`, `Drivers.tsx`, `DriverDetail.tsx`

Written **before** any React code, per the standing rule. The vehicle detail screen shipped with six
of eight sections missing because it was built from a mockup instead of the Flask template, and every
test passed.

---

## 1. Flask `user_form.html` — the Mobile access checkbox

Lines 79–87, sitting **between** the Roles multi-select and the `is_lockout_exempt` checkbox.

| Property | Flask value |
|---|---|
| Control | Checkbox (`BooleanField`) |
| Label | `Mobile access (Android field app)` |
| Help text | "Allows this account to sign in on the FMS Field Android app. Controls the CHANNEL only — what the person may do once inside is decided by their roles, exactly as it is in the browser. Leaving this off does not restrict their web access in any way." |
| Icon | `bi bi-phone` before the help text |
| Default on create | unchecked |
| On edit | reflects stored value; unchecking revokes |

**React gap:** `UsersPage`'s create/edit modal has `username, email, password, first_name,
last_name, employee_id, branch_id, role_ids`. No `mobile_access`.

## 2. Flask `users_list.html` — the Mobile column

Header row (line 16): `Username | Name | Email | Roles | Active | **Mobile** | Status | Last login |`

Cell: `<span class="badge text-bg-info"><i class="bi bi-phone"></i>Yes</span>` when true, an em-dash
in muted text when false. A dash rather than a red "No" — most accounts are web-only by design, and
a warning badge on every one of them would train people to read past the column.

**React gap:** columns are `USER | EMAIL | BRANCH | ROLES | STATUS | ACTIONS`.

> ⚠ **This screen has already diverged from Flask deliberately.** It is a redesigned admin UI, not a
> strict port: different columns, different order, different casing, a modal instead of a page.
> "Flask is the specification" is about not *omitting* fields, not about reverting an intentional
> redesign. **Scope of this work: add the `mobile_access` field and column. Do not restructure the
> screen.** If the client wants the React admin screen brought to strict parity, that is a separate,
> larger decision.

## 3. Flask `driver_form.html` — the System Account picker

Lines 162–176, inside `FormSection "Organizational Scope"`, **between Department and Section**.

| Property | Flask value |
|---|---|
| Control | `<select name="user_id" class="form-select fms-select2">` — searchable single-select |
| Empty option | `— None —` (value `""`) |
| Option label | `{username} — {full_name}` |
| Options source | Active users, ordered by username. **Already-linked accounts are NOT filtered out** — see below |
| Help text | "The FMS login this person signs in with. Optional — most assignees never have one. Linking does not by itself grant the Android field app; that is the Mobile access switch in User Maintenance." |

Already-linked accounts are deliberately still offered. Filtering them would leave an administrator
staring at an empty picker with no way to discover that the name they want is held elsewhere; the
duplicate error names the assignee holding it, which is the information they actually need.

**React gap:** `DriverForm.tsx`'s Organizational Scope section runs `branch_id → department_id →
section → cost_center → position → job_title → employment_status → …`. No `user_id`.

## 4. Flask `driver_list.html` — the System Account column

Header (line 19): `Employee No. | Name | License No. | License Expiry | Type | Branch | **System
Account** | Status |`

Cell: `<span class="badge text-bg-light border"><i class="bi bi-person-badge"></i>{username}</span>`,
or a muted em-dash.

**React gap:** `Drivers.tsx` runs `Employee No. | Name | License No. | License Expiry | Type | Branch
| Status | Actions`. Insert between Branch and Status — same position as Flask.

## 5. Flask `driver_detail.html` — **no change**

I checked. `driver_detail.html` does **not** display the linked account.

**Correction to my earlier statement:** I previously listed `DriverDetail.tsx` as a fourth gap. It is
not. Parity means matching Flask, and Flask does not show it here. Adding it to React would be a new
feature invented during a parity task — the mirror image of the mistake this process exists to
prevent. **No change to `DriverDetail.tsx`.**

---

## 6. Backend gap that blocks item 3

The Flask picker is populated server-side by `_linkable_users()`, reachable by anyone holding
`driver.update`.

React would have to call `GET /api/v1/admin/users`, which is guarded by **`user.view`**.

A Fleet Officer who maintains assignees very plausibly does not hold `user.view` — it is a System
Administration permission. For them the React picker would silently render empty while the Flask form
works, which is the worst outcome: not an error, just a control that appears broken.

**Required first:** `GET /api/v1/drivers/linkable-users`, guarded by `driver.update`, returning
`{id, username, full_name}` for active users ordered by username. It exposes strictly less than
`/admin/users` (three fields, no email, no roles, no branch, no login history), so widening the
permission is not widening the exposure.

---

## Work items, in order

| # | Item | Where |
|---|---|---|
| 1 | `GET /api/v1/drivers/linkable-users` under `driver.update` | Flask |
| 2 | `mobile_access` checkbox in the user modal + `MOBILE` column | `AdminRbac.tsx` |
| 3 | `user_id` picker in Organizational Scope, between Department and Section | `DriverForm.tsx` |
| 4 | `System Account` column between Branch and Status | `Drivers.tsx` |
| 5 | — | `DriverDetail.tsx` — **no change** |

The users API already returns and accepts `mobile_access` (v231). The driver API already returns
`user_id` and `username` (v229). Item 1 is the only backend work.

## Tests, written from this audit

- Users list renders a Mobile column; a user with `mobile_access` shows the badge
- The user modal posts `mobile_access` on create and on edit, and **an unchecked box revokes**
- `DriverForm` renders a `user_id` select whose first option is `— None —`
- `DriverForm` submits `user_id`, and submits `""` when cleared
- `Drivers` renders a System Account column showing the username, an em-dash when unlinked
- `linkable-users` is reachable with `driver.update` and **without** `user.view`

## Standing guards to re-check after

- No Bootstrap form classes (`form-control`, `form-select`) without matching CSS — React uses its own
  `ff__`/`drv-` classes and must not acquire Bootstrap ones by copy-paste from the Jinja template
- Every CSS class referenced in JSX must exist
- Tailwind class presence verified in the built output
