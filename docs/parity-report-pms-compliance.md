# Parity audit — PMS Compliance / Due Report

Source of truth: `app/modules/system_admin/routes.py::report_pms_compliance`
and `generate_pms_compliance_xlsx` in `app/core/reporting/generators.py`.
React inherits this behaviour; it does not approximate it.

## Permission

`reportpmscompliance.view` — already registered. A fleet-wide reporting
export is a different disclosure from browsing one vehicle at a time, so
`vehicle.view` must NOT imply it. Same rule the Flask route follows.

## The should_run gate (do NOT skip this)

The Flask route deliberately does **not** run the report just because
the page opened. `get_all_due_vehicles()` evaluates the PM due status of
every active vehicle against every applicable schedule — one of the
heaviest queries in the system on a real fleet. Opening the page by
accident, or navigating back to it, must not pay that cost for a result
nobody asked for.

It runs only when `generate=1` OR any filter is set (so a bookmarked
filtered URL still works). The React screen must reproduce this: no
fetch on mount, fetch on Generate or when arriving with filters in the
query string.

## Filters (query-string keys, shared by screen and export)

- `branch_id` — int
- `vehicle_type_id` — int
- `status` — one of the computed status strings
- `maintenance_type_id` — int; a vehicle with no applicable schedule has
  no maintenance type and is correctly excluded, never shown under
  whichever type happened to be selected

Org scope is applied server-side regardless of filters:
`scope_svc.covers(user.id, branch_id=...)`. An approver scoped to one
branch must not see another branch's vehicles in the report — the same
rule just fixed in `list_for_user`.

## Columns (exact, in order)

Plate No. · Branch · Cost Center · Make · Model · Maintenance Type ·
Next Due (km) · Current Odometer · Next Due Date · Status

`Plate No.` falls back to conduction number. Branch, cost center,
maintenance type and the due figures fall back to `—` when absent.

## Endpoints to add

```
GET /reports/pms-compliance            -> { rows, generated_at } | { rows: null }
GET /reports/pms-compliance/export.xlsx -> the same spreadsheet
```

`rows: null` (not `[]`) when the gate said not to run, so the screen can
tell "not generated yet" from "generated, nothing matched" — the Flask
template draws that same distinction (`rows is None` vs empty).

Export reuses `generate_pms_compliance_xlsx` with the same filters, so
the file and the screen cannot disagree. Same equivalence principle as
the generic approval endpoints.

## generated_at

Included so a printed or downloaded report can be told apart from one
run last quarter. The Flask template stamps it; the JSON carries it.
