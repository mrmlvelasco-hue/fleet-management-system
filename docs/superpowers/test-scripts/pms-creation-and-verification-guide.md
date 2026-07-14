# Testing Guide — Creating and Verifying a PMS (Preventive Maintenance Schedule)

This guide covers two paths — **A) using a vehicle already in your database**
(leveraging the VEMS-imported PM Templates, which should auto-match) and
**B) building everything from scratch** for a clean test. Do either one,
or both.

**Prerequisite**: Maintenance Orders need a `MO` Document Type with a
numbering scheme configured before any can be created (Sidebar →
Document Types). If you've already tested Maintenance Orders before,
this is already set up — skip ahead.

A companion CLI command lets you trigger the daily due-check manually
without waiting for Celery:
```
flask pm run-due-check
```

---

## Path A — Test with an existing vehicle + the VEMS-imported data

Use this if you've already run `import_vems_makemodel` and `import_pms`.

1. **Master Data → Vehicles** — open (or create) a vehicle whose Brand/Model
   matches something in the VEMS import (e.g. any Toyota/Ford/Honda model
   from the sheet). Note its **Current Odometer**.
2. **Sidebar → PM Templates → PMS Profiles** — search for that vehicle's
   Brand/Model. If found, click in to see its packages (e.g. "10,000 km",
   "20,000 km" service tiers) — this is the auto-imported cycle.
3. Set the vehicle's **Current Odometer** to a value close to one of that
   profile's `interval_km` values (e.g. if a package is 10,000 km, set
   the vehicle's odometer to 9,600 — inside the default 500 km warning
   window).
4. **Dashboard** → confirm the **Maintenance** KPI card increases by 1,
   and the vehicle appears in the **Vehicles Due for Maintenance** table
   with a `DUE_SOON` badge.
5. Push the odometer past the interval (e.g. 10,050) → refresh Dashboard
   → badge should now read `OVERDUE`.
6. Run `flask pm run-due-check` → since VEMS-imported templates default
   to **AUTO_SCHEDULE** (no auto-created Maintenance Order), confirm:
   - No new Maintenance Order was created for this vehicle
   - An in-app notification was created (Sidebar bell icon, or check
     Sidebar → Notification Rules has `pm_overdue`/`pm_due_soon` rules
     configured with a recipient — if none exist yet, add one first so
     you have something to see)

---

## Path B — Build a PMS from scratch (clean, fully controlled test)

### 1. Create the vehicle
**Master Data → Vehicles → New**
- Brand/Model: pick any existing ones, or create a new Vehicle Brand/Model
  first (**Master Data → Vehicle Brands → New**)
- Conduction/Plate Number: anything unique
- Current Odometer: `4800`
- Branch: any existing branch

### 2. Create the PM Template (the recurring interval rule)
**Sidebar → PM Templates → New Template**
- Brand / Model: select the same ones from step 1 (or leave blank + use
  Vehicle Type instead, to match by category rather than exact model)
- Maintenance Type: pick an existing one, or create one first at
  **Master Data → Maintenance Types → New** (e.g. code `5K-PMS`,
  category `PREVENTIVE`)
- Trigger Mode: `KM only`
- Trigger KM: `5000`
- **Scheduling Policy** — leave as default (`Auto Generate Schedule` /
  `Actual Completion`) for now; we'll test `Auto Generate Maintenance
  Order` later in step 6
- Save

### 3. Create the PM Scope Template (the checklist)
**Sidebar → PM Templates → PM Scope Templates → New Template**
- Name: `5,000 KM Checklist`
- Maintenance Type: the same one from step 2
- Ties to specific PM Template: select the template you just created
  (this links the checklist to *this specific* interval, not just the
  Maintenance Type generically)
- Add 2–3 activity rows (e.g. `OIL` / "Change engine oil", `FILTER` /
  "Replace oil filter")
- Save

### 4. Verify the match and due status
- **Master Data → Vehicles** → open your test vehicle → confirm it now
  shows up correctly matched (if you set Brand/Model on the PM Template,
  check **PM Templates → PMS Profiles** or just check the Dashboard)
- **Dashboard** → the vehicle should appear in **Vehicles Due for
  Maintenance** with status `DUE_SOON` (4,800 is within 500 km of the
  5,000 km trigger)
- Click the vehicle's name in that table → confirms the click-through
  works and lands on the Vehicle detail page

### 5. Confirm the "no auto-MO by default" behavior (PMS-3)
```
flask pm run-due-check
```
- **Transactions → Maintenance Orders** → confirm **no** order was
  auto-created for this vehicle (this is the new default — Option B)
- Check for an in-app notification instead (bell icon, top right)

### 6. Test the "Auto Generate Maintenance Order" policy
- Go back to **PM Templates**, open your template, click **Edit**
- Change **Next PMS Generation** to `Auto Generate Maintenance Order`
- Save, then run `flask pm run-due-check` again
- **Transactions → Maintenance Orders** → this time a **DRAFT** order
  should exist, linked to your vehicle and the PM Template
- Run the command again → confirm it does **not** duplicate (idempotent
  — it skips vehicles that already have an open order for that
  Maintenance Type)

### 7. Test approval-gating on the PM Template itself (optional)
If you want the PM Template's generation to require sign-off before
anything happens:
- **Document Types** → edit the relevant Document Type → check "Requires
  Approval" (this affects the *Maintenance Order* approval workflow, not
  the PM Template match itself — set up an Approval Path + Matrix the
  same way as any other module, see the earlier Maintenance Order test
  guide for the full steps)

### 8. Complete the Maintenance Order and confirm due-date recalculation
- Open the DRAFT Maintenance Order from step 6 → **Submit** → (if no
  approval required) → **Start Work** → check off the checklist items →
  **Complete**, entering an odometer reading (e.g. `5280`)
- **Dashboard** → the vehicle should drop off "Vehicles Due for
  Maintenance" (next due is now recalculated from 5,280)
- Go back to the PM Template → **Edit** → change **Next Due Calculation
  Method** to `Based on Original Schedule` → Save
- Push the vehicle's odometer to `10,050` → Dashboard should show
  `OVERDUE` at exactly 10,000 (not 10,280) — confirming Method B rounds
  to the interval multiple rather than the actual completion point

---

## Quick Reference — Where Things Live

| Screen | URL |
|---|---|
| Vehicles | `/master/vehicles` |
| PM Templates | `/admin/pm-schedules` |
| PMS Profiles (grouped view) | `/admin/pms-profiles` |
| PM Scope Templates | `/admin/pm-scope-templates` |
| Maintenance Orders | `/transactions/maintenance-orders` |
| Dashboard | `/` |

## Known Limitations to Expect (not bugs)

- The **Vehicles Due for Maintenance** dashboard table only shows the
  *first* applicable schedule per vehicle, not every maintenance type
  that might be due simultaneously
- **Administrator Selection** (the third Due Calculation Method option)
  currently falls back to Actual Completion — there's no completion-time
  prompt yet to let a human choose between methods on the spot
- Celery beat's actual recurring schedule (so this runs automatically
  every day/hour in production) is a deployment-configuration step, not
  something testable in this environment — use `flask pm run-due-check`
  to simulate it manually
