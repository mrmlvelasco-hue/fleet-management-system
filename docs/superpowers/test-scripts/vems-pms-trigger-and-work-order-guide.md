# Testing Guide — VEMS Import, PMS Trigger Logic, and Creating Work Orders

## Part 1 — VEMS Import Scripts (testing data)

Both scripts live in `scripts/` and support a dry-run preview before
writing anything.

```python
# From `flask shell` (or Claude Code / your IDE's Python console with the
# app context active):
from scripts.import_vems_makemodel import import_make_model
from scripts.import_vems_pms import import_pms

# Step 1 — Make/Model sheet (34 brands, 338 models)
import_make_model("VEMS_Masterdata_for_vehicle.xlsx", dry_run=True)   # preview
import_make_model("VEMS_Masterdata_for_vehicle.xlsx", dry_run=False)  # commit

# Step 2 — PMS sheet (4,991 raw rows -> ~1,486 distinct packages)
import_pms("VEMS_Masterdata_for_vehicle.xlsx", dry_run=True)          # preview
import_pms("VEMS_Masterdata_for_vehicle.xlsx", dry_run=False)         # commit
```

Both are **idempotent** — safe to re-run; already-imported brands/models/
packages are skipped, not duplicated. `import_pms` deliberately **excludes**
"Vehicle Registration" rows (that's LTO renewal — handled by our own
Vehicle Registration module, not PM Templates).

**Verify the import landed correctly:**
- Sidebar → Master Data → Vehicle Brands — should show 34 brands
- Sidebar → Master Data → PM Templates — should show ~1,486 rows, each
  labeled with a distinguishable interval (e.g. `[HILUX-DIESEL #2] —
  10,000 km`) so packages sharing a vehicle are no longer identical-looking
- Sidebar → Master Data → PM Templates → PMS Profiles — grouped view;
  search a known Make/Model (e.g. Hyundai Getz) to see its full package
  cycle in one place

---

## Part 2 — What Actually Triggers "Next PMS"

This trips people up because there are **two separate mechanisms** working
together — understanding both is the key to testing this correctly.

### Mechanism 1 — Due Status (always live, no trigger needed)
`PMDueCalculationService` computes a vehicle's due status **on every page
load** — Dashboard, Vehicle detail, the due-vehicles widget — by comparing:
```
next_due_km = last_completed_service_km + PM_Template.interval_km
```
("last completed service" = the most recent COMPLETED Maintenance Order
for that vehicle + maintenance type; if none exists yet, it starts from 0.)

There's **nothing to trigger** here — it's just a live calculation. A
vehicle can show `DUE_SOON`/`OVERDUE` the moment its odometer crosses the
threshold, with zero scheduled jobs involved.

### Mechanism 2 — What happens once something IS due
This is the part that's actually configurable, via each PM Template's
**Next PMS Generation** field:

| Setting | Behavior |
|---|---|
| `MANUAL` | Nothing automatic — fleet staff see it's due (Dashboard/notification) and create the Maintenance Order by hand |
| `AUTO_SCHEDULE` (default) | Same as Manual, but the system fires an in-app notification automatically. Still no Maintenance Order created |
| `AUTO_MO` | Automatically creates a **DRAFT** Maintenance Order the moment the vehicle becomes due |

**The check itself only runs when triggered** — either by Celery beat on
a schedule (not wired up in this environment) or manually via:
```
flask pm run-due-check
```
Run this any time you want to simulate "a day has passed" during testing.

### Step-by-step test
1. Pick (or create) a vehicle + PM Template pair, push the vehicle's
   odometer past the interval so it's `OVERDUE`
2. Check the PM Template's **Next PMS Generation** setting (Edit screen)
3. Run `flask pm run-due-check`
4. **If MANUAL/AUTO_SCHEDULE**: confirm no Maintenance Order was created,
   but a notification exists (bell icon)
5. **If AUTO_MO**: confirm a DRAFT Maintenance Order now exists, linked
   to that vehicle and PM Template
6. Run the command again — confirm it's idempotent (no duplicate order
   created for a vehicle that already has one open)

---

## Part 2b — "Whichever Comes First" (HYBRID) Deep Dive

A `HYBRID` PM Template (e.g. "5,000 KM **OR** 6 Months") computes the KM
condition and the calendar condition **independently**, then reports
whichever is more urgent. Neither one "blocks" the other — the vehicle
becomes due the moment *either* threshold is crossed.

### Test it directly via the UI
1. **Master Data → Maintenance Types → New** — create one, category
   `PREVENTIVE`
2. **PM Templates → New Template** — Trigger Mode: `Hybrid (whichever
   comes first)`, Trigger KM: `5000`, Trigger Days: `180` (~6 months),
   Vehicle Type: any
3. **Master Data → Vehicles → New** — create a test vehicle, Vehicle Type
   matching the template, Current Odometer: `5200`
4. **Transactions → Maintenance Orders → New** — create and Complete one
   for this vehicle at odometer `0`, completed 60 days ago (backdate the
   Completed Date field)
5. **Dashboard** — this vehicle should now show `OVERDUE`, even though
   only 60 of the 180 allowed days have passed — because the **KM**
   condition (5,200 ≥ 5,000) tripped first
6. Repeat with a second vehicle: odometer `800`, but complete its last
   service **190 days ago** instead — this one goes `OVERDUE` too, but
   this time because the **calendar** condition tripped first, even
   though it's barely used 800 of its 5,000 km allowance

### Executable version (flask shell)
```python
from datetime import date, timedelta
from app.core.maintenance.due_calculation_service import PMDueCalculationService

# After creating the vehicle/template pair and a COMPLETED Maintenance
# Order as your "last service" baseline:
status = PMDueCalculationService().get_due_status(vehicle)
print(status["status"])        # OVERDUE / DUE_SOON / GOOD
print(status["next_due_km"])   # the KM side of the calculation
print(status["next_due_date"]) # the calendar side of the calculation
```
Both `next_due_km` and `next_due_date` are always computed and returned
together for a HYBRID template — inspect both to see which one actually
tripped the status, even when only one of them has crossed its threshold.

A full working example with all 4 scenarios (KM-first, calendar-first,
neither, both-at-once) is in
`tests/unit/test_hybrid_whichever_comes_first_demo.py` — run it directly:
```
pytest tests/unit/test_hybrid_whichever_comes_first_demo.py -v
```

---

## Part 3 — Creating a Normal (Corrective/Unscheduled) Work Order

Not every Maintenance Order comes from a PM trigger — a driver reporting
a flat tire or a strange noise needs a manual, one-off order with no PM
Template involved at all.

1. **Sidebar → Transactions → Maintenance Orders → New**
2. **Vehicle** — search and select (only vehicles within your org scope
   will appear)
3. **Maintenance Type** — pick one with category **CORRECTIVE** (e.g.
   "Corrective Maintenance" — create one first at Master Data →
   Maintenance Types if none exists yet, category = CORRECTIVE)
4. **PM Scope Template** — leave this **blank**. This field only applies
   to preventive checklists; a corrective order doesn't need one
5. Fill in Scheduled Date, Odometer, Description of the issue, optionally
   assign a Mechanic/Vendor and Estimated Cost
6. **Save** → the order starts as **DRAFT**
7. **Submit** → goes to Pending (or straight to actionable if no approval
   configured for Maintenance Orders)
8. **Start Work** → moves to In Progress
9. **Complete**, entering the final odometer and actual cost

This is identical to the PM-triggered flow from Part 2 once you reach
Submit — the only difference is *how* the DRAFT order came to exist
(manually created by a person vs. auto-created by `AUTO_MO`).

---

## Quick Reference

| Screen | URL |
|---|---|
| Maintenance Types | `/master/maintenance-types` |
| Maintenance Orders | `/transactions/maintenance-orders` |
| PM Templates | `/admin/pm-schedules` |
| PMS Profiles | `/admin/pms-profiles` |
| Dashboard | `/` |

Manual due-check trigger: `flask pm run-due-check`
