# Parity audit — Maintenance Cost Summary

Source of truth: `report_maintenance_cost_summary` in
`app/modules/system_admin/routes.py`, plus
`generate_maintenance_cost_summary_xlsx` and
`VehicleBudgetService.get_budget_status`.

This report has TWO sections, and the generator only covers the first.
Building against the generator alone would silently drop the second.

## Permission

`reportmaintenancecost.view` — already registered.

## Section 1 — Cost detail (what the generator exports)

No run gate: `MaintenanceOrder.query` filtered to `status="COMPLETED"` is
cheap regardless of fleet size, unlike PMS's full schedule scan. Runs on
open.

### Filters (query-string, shared by screen and export)

- `branch_id` — int, via `vehicle.has(branch_id=...)`
- `vehicle_type_id` — int, via `vehicle.has(vehicle_type_id=...)`
- `date_from` / `date_to` — against `completed_date`, inclusive both ends
- `plate_number` — substring match, case-insensitive, against EITHER
  `plate_number` OR `conduction_number`. Applied in Python after the SQL
  query, not in SQL — matches the Jinja route exactly, needed because it
  checks two columns with an OR-across-fallback that plate_number alone
  can't express in the query builder used here.

Org scope applies after the plate filter, same `covers()` rule as every
other report.

Ordered by `completed_date` descending.

### Columns (exact, in order)

MO Number · Vehicle (plate/conduction — brand model, one combined
string) · Branch · Category · Maintenance Type · Completed Date ·
Actual Cost

`MO Number` falls back to `"(draft)"` — a COMPLETED order can still have
no document number if numbering wasn't finalized, per the generator.

### Total

A single number: `sum(actual_cost or 0)` over the FILTERED orders. The
generator writes it as a bold TOTAL row in the sheet. The API must
return it as a field, computed server-side over the same filtered set
the rows come from — the screen must never recompute this by summing
the rows it received, because that duplicates business logic that
already lives in one place and could silently diverge from the
export's total if the two summed different things (e.g. a screen that
paginates and re-sums only the visible page).

## Section 2 — Budget Utilization by Vehicle (NOT in the generator)

Built from **distinct vehicles appearing in the filtered detail rows**,
not a separate unfiltered query — this is explicit in the Flask
docstring: the summary must always match whatever the filters narrowed
the detail rows down to. First occurrence per vehicle wins (order
preserved from the `orders` list, i.e. most-recently-completed first).

For each such vehicle, call `get_budget_status(vehicle)` and include the
row only if `applicable` is `True`. `applicable: False` means budget
tracking doesn't apply — no CAR_PLAN/COMPANY_OWNED classification, or no
acquisition/delivery date to compute an age-year from — and must be
treated as "not applicable", never as an error or as over-budget.

### Budget row shape

```
mode: "PER_YEAR" | "ACCUMULATED"
classification: "CAR_PLAN" | "COMPANY_OWNED"
current_year: int
period_start, period_end: date
budget, spent, remaining: decimal
over_budget: bool  (remaining < 0)
```

Critical: `spent` is **NOT** derived from the report's filtered orders.
It comes from `_spent_in_period`, a fresh query over the vehicle's own
budget-year window (anchored to its delivery/acquisition date, not the
report's date_from/date_to). A vehicle's budget year and the report's
date range are two independent things that happen to overlap sometimes
— conflating them would show a wrong remaining-budget figure whenever
someone filters the report to a narrower window than the vehicle's
actual budget period.

This section has no equivalent in the XLSX export — `generate_
maintenance_cost_summary_xlsx` does not include it. The screen shows
something the spreadsheet does not. That's a genuine, pre-existing
asymmetry in Flask, not something to "fix" by adding it to the
generator — that would be scope beyond parity.

## Endpoints to add

```
GET /reports/maintenance-cost              -> { rows, total, budget_rows, generated_at }
GET /reports/maintenance-cost/export.xlsx  -> the same spreadsheet (detail + TOTAL only)
```

No run gate. `rows` is always a list.
