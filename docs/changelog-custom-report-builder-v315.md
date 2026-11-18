# Custom Report Builder enhancement — backend v315 / React v253

Date: 2026-11-18. Baselines left untouched: backend `v314`, React `v252`.

## What changed for users

- **Redesigned builder** (React, per the approved mockup):
  - Five numbered sections: definition and data source; output columns; filters; sort and grouping; schedule and delivery.
  - A side panel with a builder guide whose steps tick off as you complete them, plus a live report summary.
- **Searchable field catalog**:
  - Fields are grouped by category with counts; the catalog has a search box, and each field shows its type.
  - Select all / Clear all, and a **Selected columns** list you can reorder by dragging, or with the arrow keys on the grip handle.
- **Filters**:
  - **AND/OR** switch.
  - Operators matched to the field type: equals, does not equal, contains, does not contain, greater/less than (or equal), **between**, is empty, is not empty.
  - Date pickers for dates, with **between** as a date range; Yes/No for flags.
- **Sort and Group by**:
  - Grouping adds a heading row per group with its row count and subtotals.
  - Totalled columns (amounts, quantities) get a grand total.
- **Preview** shows real, paged data (25 rows per page) with the total record count. An empty result says so, and an invalid filter shows the server's message ("'abc' is not a number for Quantity (L).").
- **Running a saved report** is paged (50 rows), shows groups and totals, and offers **Export CSV** and **Export XLSX**.
- **Schedule & delivery**:
  - Daily / Weekly / Monthly, as CSV or XLSX, to the given recipients.
  - Delivered by the existing `flask report run-due` job; the schedule is listed under Scheduled Reports.
  - It runs with the access of the person who set it.
- **Two new data sources**: Fuel Transactions and Purchase Requisitions (PR), with their fields listed below.

## Data sources

The existing sources — Vehicles, Maintenance Orders, Vehicle Registrations and Trip Tickets — keep every source key and field key. Each gains categories and a few more fields:

| Source | New fields |
|---|---|
| Vehicles | transmission, branch code, company, OR/CR no., registration expiry, created |
| Maintenance Orders | branch and company (through the vehicle), created |
| Vehicle Registrations | registration type and date, validity, OR/CR cost, odometer at registration, model, branch, company, created |
| Trip Tickets | return, distance (in − out), driver, brand, model, branch, company, created |

**Fuel Transactions** (`fuel_transactions`, needs `fuel.view`):
- **Transaction**: no., date and time, reference no., source, remarks, needs review, anomaly flags.
- **Vehicle**: plate, conduction no., vehicle, brand, model, type, status, vehicle fuel type.
- **Driver**: name; employee no. and position (both need `driver.view`).
- **Fuel**: fuel type; quantity (3 decimals).
- **Cost**: unit price (4 decimals), total amount (2 decimals), cost per km.
- **Odometer & efficiency**: reported and validated odometer, status, confirmed, note, distance, km per litre.
- **Supplier / station**: station, supplier name and code, fuel card no. and provider.
- **Organization**: branch, branch code, company.
- **Audit**: created and created by, import file, batch, imported at and by.
- Fuel transactions have **no status column**, so none is offered. "Needs review" and "Odometer status" are the real status fields.

**Purchase Requisitions** (`purchase_requisitions`, needs `purchaserequest.view`):
- **PR header**: PR no., PR date (the date created — PRs have no separate request date), required date, description, justification, status.
- **Requester**: name, username, employee ID.
- **Organization**: department, plus branch, branch code and company (through the department).
- **Vehicle & Work Order**: MO no., MO status, plate, vehicle. All need `maintenanceorder.view`.
- **Item details**: item description, quantity, unit cost, line total.
- **Supplier**: name, code, type, TIN (TIN needs `vendor.view`).
- **Approval**: approval status, current level, approved by, approval date.
- **Financial**: PR amount, no. of items, items total.
- **Audit**: created, last updated, created by.
- **Header and lines**:
  - A PR is one header with many lines. Choosing any item column gives one row per line, and the **PR amount, item count and items total then appear on the first line only**. Totals and subtotals are therefore never multiplied by the number of lines.
  - Filtering on an item without showing item columns keeps **one row per PR**, using an EXISTS test.
- **Work Order link**: the link to the MO lives on the MO (`maintenance_orders.purchase_request_id`), so the report joins the lowest-numbered MO that points at the PR. That guarantees one row per PR.

## Security and access

- **Org scope applies everywhere.** Before v315 a custom report applied no organisational scope. Now each source applies the same scope as its own list screen:
  - Vehicles: `VehicleService.list_page` rules, including assigned-vehicle restriction.
  - Transactions: each module's `_apply_visibility_filter`.
  - Fuel: `fuel_scope_ids`.
  - **A branch-scoped user who ran a report before may now see fewer rows. That is the fix, not a regression.**
- **Field-level permissions**:
  - Fields from another module declare that module's permission. Such fields are left out of the catalog for users without it, and are refused on preview, save and run.
  - The catalog only lists sources the user can read.
- **No raw SQL**:
  - Every field is code in `report_builder.py`. No SQL or expressions come from the user, and filter values are bound parameters (LIKE wildcards are escaped).
  - Preview and save validate the whole definition server-side: unknown fields and operators, operators that don't fit the type, missing or invalid values, and permissions.

## Fixes

- **Trip Tickets "Trip Date"**: the field pointed at a column that does not exist (`trip_date`), so it was always blank and filtering or sorting on it would have failed. The key `trip_date` is kept for saved reports; it now reads the departure date and time.
- **Editing duplicated reports**: editing in the React screen POSTed a **new** report every time. Edits now use the new `PUT /admin/custom-reports/<id>` and keep the same report ID.
- **Edit form lost settings**: the edit screen did not load filters, sort, max rows or recipients. It now loads the whole definition.
- **Amounts exported as text**: Excel export wrote amounts (Numeric columns) as text. They are now numbers with their precision and number formats, plus subtotals and a total.
- **Date range end date**: a date-range filter on a date-and-time column now includes the whole last day.

## Compatibility

- **Saved reports keep working** (tests cover this):
  - They open, run, export and edit.
  - `filter_logic` defaults to AND and `group_by` to none.
  - Rows left half-filled by the older Flask form are still skipped when a saved report runs.
- **Flask screens** (`/system-admin/custom-reports`) are unchanged and keep working. Editing a report there no longer resets its AND/OR logic or grouping.
- **API responses** keep all previous keys; new keys are additions. A deleted report now returns 404 instead of its data.
- **Hard limit**: 5,000 rows per report, as before. Max rows above 5,000 are saved as 5,000.

## API (all `/api/v1`)

| Method | Path | Permission | Change |
|---|---|---|---|
| GET | `/admin/custom-reports` | customreport.view | `sources` filtered per user, with `categories`, field `category`/`kind`/`scale`/`aggregate`, plus `operators`, `operators_by_kind`, `max_rows` |
| POST | `/admin/custom-reports/preview` | customreport.manage | `filter_logic`, `group_by`, `page`, `per_page`; returns `total`, `pages`, `totals`, `groups`; 400 with a message on invalid input |
| POST | `/admin/custom-reports` | customreport.manage | strict validation; optional `schedule` |
| GET | `/admin/custom-reports/<id>` | customreport.view | adds `filter_logic`, `group_by`, `schedule` |
| PUT | `/admin/custom-reports/<id>` | customreport.manage | **new**: edit in place |
| GET | `/admin/custom-reports/<id>/run?page=&per_page=` | customreport.view | paged; exact decimals as strings |
| GET | `/admin/custom-reports/<id>/export?format=csv\|xlsx` | customreport.view | **new** |
| POST | `/admin/custom-reports/<id>/email` | customreport.view | **new**: queue an emailed copy |

## Database

- **Migration `b8d4f2a6c915`** (revises `a7c3e9f1d204`):
  - Adds `custom_reports.filter_logic` (VARCHAR(3), NOT NULL, default `'AND'`) and `custom_reports.group_by` (VARCHAR(60), NULL). Nothing else is altered.
  - Verified up → down → up with an existing report row.
- **Schedules** reuse the existing `scheduled_reports` table: report code `CUSTOM:<report id>`, with format and run-as user in `filters_json`.

## Tests

- **Backend**, new:
  - `tests/unit/test_custom_report_sources.py` (30 tests): every field of every source selects, sorts, groups and filters; Fuel precision and totals; whole-day date ranges; AND/OR; does not contain / is empty; grouping subtotals; paging; empty results; fuel scope; PR one row per header; lines without duplicated totals; EXISTS item filter; field permissions; validation messages; CSV/XLSX.
  - `tests/unit/test_custom_report_api.py` (17 tests): create and run for all six sources; catalog; preview paging and errors; edit in place; strict save; CSV/XLSX export; branch scope on every source; unauthorized fields; schedule saved, delivered as CSV and stopped; pre-v315 reports; Flask edit keeping AND/OR; delete stops the schedule.
  - The 18 existing `test_custom_report_builder.py` tests pass unchanged.
- **Mutation checks** — each test fails when its rule is broken:
  - first-line-only header amount;
  - EXISTS on item filters;
  - whole-day date range;
  - field permission;
  - org scope;
  - Flask edit keeping AND/OR.
- **Full backend suite**, in 8 batches: 4,379 passed.
  - 8 failures are identical on the untouched `v314` (`test_mo_detail_reskin` ×4, `test_maintenanceorder_print_report`, `test_maintenance_invoice_ui`, `test_mo_print_multi_invoice_grouping`, `test_document_edit_policy[atd]`).
  - 1 was a crashed test worker (memory); that file passes when run alone.
- **React**: `src/__tests__/custom_report_builder.test.tsx` (10 tests). Both `npm run build` and `npm run build:mobile` pass.
- **Browser** (Chrome via Playwright, real Flask and SQLite):
  - Built a monthly Fuel report grouped by branch, previewed, saved, exported CSV and XLSX, and reopened it for editing.
  - Built a PR report with item lines: the PR amount total equals the line total.
  - Checked the layout at a phone width.
- **Pre-existing React failures**: `mobile_releases_admin`, `mo_new_prefill`, `vehicleform` and `vehicleform.parity` (15 tests) fail identically on `v252` without these changes.

## Deploying

1. Backend `v315`: `flask db upgrade` (adds the two columns).
2. React `v253`: build and deploy.
3. **For schedules**: make sure `flask report run-due` is already scheduled (cron / Task Scheduler), as for the existing scheduled reports.
4. **Roles**: Fuel Transactions needs `fuel.view`; Purchase Requisitions needs `purchaserequest.view`.
