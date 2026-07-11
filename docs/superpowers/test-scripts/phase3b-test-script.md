# Phase 3b — Manual Test Script (PM Scheduling, Maintenance Orders, Tire/Battery Transactions)

Prerequisites: Phase 1–3a already seeded and working (admin login, at least one
Branch, Vehicle Type, Vehicle, Vendor already exist). Log in as an account with
the System Administrator role (has all permissions).

## 1. PM Configuration

1. Sidebar → **PM Schedules** → New Schedule
   - Vehicle Type: pick your existing type (or "All vehicle types")
   - Maintenance Type: pick or create one first under Master Data → Maintenance Types
     (e.g. code `PMS-5K`, category `PREVENTIVE`)
   - Trigger Mode: `Hybrid`
   - Interval KM: `5000`, Interval Days: `180`
   - Save → confirm it appears in the list with both intervals shown

2. Sidebar → **PM Scope Templates** → New Template
   - Name: `5,000 KM PMS Scope`
   - Maintenance Type: same as above
   - Add activities: `OIL` / "Change Engine Oil", `FILTER` / "Replace Oil Filter",
     `BRAKE` / "Check Brakes" (use "Add Activity" button for each row)
   - Save → confirm the template list shows all 3 activity badges

## 2. Maintenance Order — Preventive with Checklist

1. Sidebar → **Maintenance Orders** → New Order
   - Vehicle: your test vehicle
   - Maintenance Type: the PMS one above
   - PM Scope Template: the one you just created
   - Scheduled Date: today
   - Odometer at Service: a number close to/over the vehicle's current reading
   - Save → you land back on the list; open the new order (eye icon)
2. On the detail page, confirm:
   - Category badge shows `PREVENTIVE`
   - A **Maintenance Checklist** card appears with your 3 activities, all unchecked
3. Click **Submit** → if the MO document type requires approval, approve it
   (or it auto-approves if `requires_approval` is off for MO)
4. Click **Start Work** → checklist items should now show a "Mark Done" button
5. Try clicking **Mark Complete** action... it isn't visible yet — that's
   correct, completion only shows once work has started; instead:
   - Tick each checklist item done ("Mark Done" on all 3)
   - Now complete the order: enter a completed date + actual cost → **Mark Complete**
   - Confirm status badge changes to `COMPLETED`
6. **Negative test**: create a second Preventive order the same way, Start
   Work, but complete it **without** ticking any checklist items — you should
   see a red flash error blocking completion ("checklist item(s) still
   incomplete").
7. Click **Print** on a completed order → a new tab opens with a formal
   printable layout including the checklist; use your browser's print dialog
   ("Save as PDF") to confirm it renders cleanly.

## 3. Maintenance Order — Corrective (no checklist)

1. New Order → Maintenance Type: create/select one with category `CORRECTIVE`
   (e.g. "Engine Repair")
   - Leave PM Scope Template blank
   - Save, Submit, Start Work, Complete (no checklist required — should
     complete immediately with just cost + date)

## 4. Vehicle Maintenance History

1. Go to Master Data → Vehicles → open the vehicle used above
2. Scroll down — confirm a **Maintenance History** card lists the completed
   order(s) with date, work order number, PM type, and cost

## 5. Tire Transactions

1. Sidebar → **Tire Transactions** → New Transaction
   - Tire: pick one from Master Data → Tires (create one first if none exist)
   - Action: `Mount`, Vehicle: your test vehicle, Date: today
   - Save → check Master Data → Tires → the tire's status should now show `MOUNTED`
2. New Transaction again: same tire, Action: `Dismount`, leave Vehicle blank
   - Save → confirm the tire's status reverts to `IN_STOCK`

## 6. Battery Transactions

1. Sidebar → **Battery Transactions** → New Transaction
   - Battery: pick one, Action: `Mount`, Vehicle: your test vehicle
   - Save → confirm battery status is `MOUNTED` in Master Data → Batteries

## 7. Permission Enforcement

1. Create a plain user with NO roles → log in as them
2. Try visiting `/transactions/maintenance-orders` → should get **403 Forbidden**
3. Assign them a role with only `maintenanceorder.view` → refresh → should now
   see the list, but no "New Order" button (no create permission) and no
   action buttons on detail pages

## 8. Automatic behaviors to spot-check

- Audit Trail (Sidebar → Audit Trail): filter table = `maintenance_orders`,
  confirm CREATE/UPDATE rows logged automatically for everything you did above
- If you configured a Notification Rule for `pm_overdue`/`pm_due_soon` (System
  Administration → Notification Rules), and a vehicle's odometer/date is past
  its schedule, the auto-generation logic (run manually via
  `flask shell` → `from app.modules.transactions.maintenance_order.tasks import auto_generate_due_maintenance_orders; auto_generate_due_maintenance_orders()`)
  should create a DRAFT order and a notification bell entry for matching users
