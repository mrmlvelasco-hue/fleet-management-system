# Parity audit — Vehicle Activity History Report

Source of truth: `report_vehicle_activity_history` / `..._export` in
`app/modules/master_data/routes.py`, `VehicleActivityHistoryService`,
and `generate_vehicle_activity_history_xlsx`.

This report is structurally unlike the other three and must not be
forced into their shape.

## What's different from PMS / Registration / Cost

| | PMS / Registration / Cost | Activity History |
|---|---|---|
| Filter | branch / type / status / date | a **list of specific vehicle ids** |
| Screen shape | one flat table | one section **per selected vehicle**, each with 3 sub-parts |
| Export shape | one sheet, one table | **one Excel sheet per vehicle** |
| "All fleet" mode | yes (default, unfiltered) | no -- meaningless with no vehicles picked |

There already exists `/vehicles/:id/activity-history`
(`vehicle.view`), used for the single-vehicle Vehicle Profile print.
This report is NOT that endpoint reused: different permission
(`reportvehicleactivity.view` — a multi-vehicle export is a different
disclosure from browsing one record), multi-vehicle in one call, and it
must include Utilization Summary which that endpoint omits.

## Permission

`reportvehicleactivity.view` — already registered.

## Selecting vehicles

`vehicle_ids` — repeated query param (`?vehicle_ids=3&vehicle_ids=7`).
Each id resolved via `VehicleService().get_visible(id, user)`, which
returns `None` for a vehicle outside the user's org scope. **Silently
dropped, not an error** — an id the person picked that has since moved
out of their scope should not blow up the whole report; it just isn't
in the result. Empty `vehicle_ids` → empty `sections`, not an error
either (the Jinja route does exactly this: an unfiltered "browse
everything" mode doesn't exist for this report).

## Per-vehicle sections (three sub-parts, all three needed)

For each resolved vehicle:

### 1. Activity rows — `get_activity_rows(vehicle)`

One row per lifecycle event, chronological (oldest first). Sources,
each independently:
- Acquisition (if `acquisition_date` set)
- Every COMPLETED Maintenance Order (PMS / Repair / Transfer /
  Disposal / other, via `_mo_activity_type`)
- Every Purchase Request reached by walking COMPLETED-or-not orders
  with a `purchase_request_id` — **deliberately not filtered to
  COMPLETED orders**, unlike everything else: a PR exists the moment an
  order is approved, and hiding it until the job finishes would hide
  committed money for exactly as long as someone is likely to be asking
  where the parts are
- Every COMPLETED tire MOUNT
- Every COMPLETED battery MOUNT

Row shape: `date, activity_type, outlet, assigned_to, description, cost,
odometer`. Rows with no date are dropped (defensive; should not occur in
practice) before sorting.

### 2. Utilization summary — `get_utilization_summary(vehicle, rows)`

Derived from the SAME activity rows just computed for that vehicle, not
a separate query — counts of transfers/PMS/repairs/tire and battery
replacements, total maintenance cost (sum of cost for those four
categories only — NOT Purchase Requests, NOT Acquisition), distinct
outlet count, vehicle age in years, current odometer.

### 3. Outlet history — `get_outlet_history(vehicle)`

Reconstructed from the **Audit Trail** on the `vehicles` table, not a
dedicated tracking table — Vehicle only stores its current branch/driver,
so this replays every historical `branch_id`/`assigned_driver_id` change
to build a from/to timeline. A vehicle with no audit history at all
(pre-dates auditing) gets a single segment showing its current
branch/driver with no `to_date`.

## Excel export — the structural divergence

`generate_vehicle_activity_history_xlsx(vehicle_ids, user)` produces
**one workbook, one sheet per vehicle**, sheet name = plate/conduction
number (truncated to Excel's 31-char limit). Each sheet has Vehicle
Master Information, then Activity History, in one continuous sheet —
NOT three separate named sections the way JSON will structure them.
This is fine: JSON is consumed by a screen that can lay things out
however helps reading; XLSX is consumed by openpyxl output that already
has its own shape. The two do not need to be byte-for-byte mirrors,
only to agree on the underlying rows and figures.

## Endpoints to add

```
GET /reports/vehicle-activity            -> { sections: [...], generated_at }
GET /reports/vehicle-activity/export.xlsx?vehicle_ids=3&vehicle_ids=7
```

No filters beyond `vehicle_ids` — no branch/date/status. No run gate:
cost is proportional to vehicles selected, which the person controls by
how many they pick.

### Section JSON shape

```
{
  vehicle: { id, plate_no, brand, model, year, engine_number,
             chassis_number, acquisition_cost, status },
  activity_rows: [ { date, activity_type, outlet, assigned_to,
                     description, cost, odometer } ],
  utilization: { total_transfers, pms_count, repair_count,
                 tire_replacements, battery_replacements,
                 total_maintenance_cost, assigned_outlets_count,
                 vehicle_age_years, current_odometer },
  outlet_history: [ { from_date, to_date, outlet, custodian } ],
}
```

## Picker

No dedicated search endpoint needed — `/vehicles` (already exists, used
elsewhere) with a text query is enough for a plate/model autocomplete.
Selection state (which vehicle ids are chosen) lives in the URL as
repeated `vehicle_ids` params, same pattern as the other three reports'
filters, so a link to "this report for these 3 vehicles" is shareable
and bookmarkable.
