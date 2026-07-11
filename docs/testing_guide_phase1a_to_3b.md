# FMS End-to-End Testing Guide (Phases 1a → 3b)

## Step 0 — Setup

```bash
pip install -r requirements.txt
flask --app wsgi db upgrade
flask --app wsgi seed all        # prompts for admin password, e.g. Admin123!
flask --app wsgi run
```

Log in as `admin` / your password at `http://127.0.0.1:5000` — you'll be asked
to change the password immediately.

---

## Step 1 — System Administration Setup

**1a. Create a full-access role and test user**
Sidebar → Roles → New Role → name "Fleet Administrator", select all permissions.
Sidebar → Users → New User → assign that role. Log out/in as this user going forward.

**1b. Document Types** (Sidebar → Document Types → New, create all 6):

| Code | Name | Approval | Auto No. | Printable |
|---|---|---|---|---|
| TT | Trip Ticket | check | check | check |
| ATD | Authority To Drive | check | check | check |
| VM | Vehicle Movement | no | check | check |
| MO | Maintenance Order | check | check | check |
| TIR | Tire Transaction | no | check | check |
| BAT | Battery Transaction | no | check | check |

**1c. Numbering Schemes** (Sidebar - Numbering - New, one per document type above)
Prefix = the code, Year on, Digits = 6, Reset = Yearly. Watch the live preview.

**1d. Approval Path** (Sidebar - Approval Paths - New)
Name "Standard One-Step" then Level 1: Role = Fleet Administrator.

**1e. Approval Matrix** (Sidebar - Approval Matrix - New, one per approval-required doc type)
TT, ATD, MO - link to "Standard One-Step", leave amount blank (amount-independent).

**1f. System Parameters** (Sidebar - System Parameters - edit)
- REQUIRE_DRIVER_FROM_MASTER -> YES
- PM_DUE_SOON_KM -> 500 (default, verify it exists)
- PM_DUE_SOON_DAYS -> 30 (default, verify it exists)

**1g. Notification Rules** (Sidebar - Notification Rules - New)
- Event submitted, Channel In-App, Recipient Submitter
- Event pm_overdue, Channel In-App, Recipient Role -> Fleet Administrator
- Event pm_due_soon, Channel In-App, Recipient Role -> Fleet Administrator

**1h. Company Profile** - fill in name/address/TIN for the printable documents' letterhead.

**1i. Lookups** - see docs/lookup_type_reference.md for the full mapping. At minimum
add a couple of FUEL_TYPE and LICENSE_TYPE values beyond the seeded defaults if you
want to test custom ones.

---

## Step 2 — Master Data Setup

1. Branches -> New: code HQ, name "Head Office"
2. Departments -> New: code OPS, name "Operations", branch = Head Office
3. Vehicle Types -> New: code LV, name "Light Vehicle", category LIGHT
4. Maintenance Types -> New: code PMS-5K, name "5,000 KM PMS", category PREVENTIVE
5. Vendors -> New: code V001, name "ABC Auto Parts", type GOODS
6. Drivers -> New: employee no. EMP-001, name, license no./expiry/type, branch = HQ
   - try uploading a license photo in the Attachments panel; confirm it shows as a thumbnail
7. Vehicles -> New: conduction number (no plate yet), type = Light Vehicle, brand/model/year,
   fuel type, branch = HQ, odometer = 4800 (deliberately close to a 5000km PM trigger)
   - upload a vehicle photo; confirm thumbnail + click-to-view works
8. Tires -> New: serial, brand, size, type RADIAL, vendor = ABC Auto Parts
9. Batteries -> New: serial, brand, capacity/voltage, vendor = ABC Auto Parts

---

## Step 3 — PM Configuration (Phase 3b)

1. PM Schedules -> New: Vehicle Type = Light Vehicle, Maintenance Type = 5,000 KM PMS,
   Trigger = HYBRID, Interval KM = 5000, Interval Days = 180
2. PM Scope Templates -> New: name "5,000 KM PMS Scope", Maintenance Type = 5,000 KM PMS,
   add activities: OIL/Change Engine Oil, FILTER/Replace Oil Filter, BRAKE/Check Brakes
3. Bulk import test: go to PM Schedules -> Import CSV, upload
   docs/pm_import_templates/pm_schedules_template.csv (create matching Maintenance Type
   codes first: PMS-5K, PMS-10K, OIL-CHG, MAJOR-PMS) - confirm the import summary shows
   created/skipped counts, then re-upload the same file and confirm it now shows all skipped
   (idempotent).

---

## Step 4 — Trip Ticket (Phase 3a)

1. Sidebar -> Trip Tickets -> New: select the vehicle, select the driver (from master, since
   REQUIRE_DRIVER_FROM_MASTER=YES), destination, purpose, departure date/time, odometer out
2. Open the detail page -> click Submit -> status becomes PENDING (or APPROVED if you set
   TT to not require approval)
3. If approval required: click Approve -> status APPROVED
4. Click Release Vehicle -> physical status RELEASED
5. Fill in return date/time + odometer in -> Mark Complete -> status COMPLETED
6. Click Print -> confirm the letterhead shows your Company Profile, and
   window.print() opens the browser print dialog (Save as PDF works)
7. Negative test: try creating a Trip Ticket with a manual driver name while
   REQUIRE_DRIVER_FROM_MASTER=YES - should be rejected with a clear error

---

## Step 5 — Authority To Drive (Phase 3a)

1. New ATD: vehicle, driver, purpose, valid from/to dates
2. Submit -> Approve -> Activate -> status ACTIVE
3. Print and confirm the license number appears correctly

---

## Step 6 — Vehicle Movement (Phase 3a)

1. New Movement: vehicle, type TRANSFER, from/to location, date
2. Submit (auto-approves since VM doesn't require approval) -> Start Transit ->
   Mark Completed
3. Negative test: try posting an invalid movement_type directly (if scripting) -
   should be rejected

---

## Step 7 — Maintenance Order with Checklist (Phase 3b)

1. New Maintenance Order: vehicle, Maintenance Type = 5,000 KM PMS, Scope Template
   = 5,000 KM PMS Scope (this generates the checklist), scheduled date, odometer = 5000
2. Detail page -> confirm the 3 checklist items (OIL/FILTER/BRAKE) appear, all unchecked
3. Submit -> Approve -> Start Work
4. Try clicking Mark Complete with checklist items still unchecked -> should be
   blocked with an "incomplete checklist" error
5. Go back, tick all 3 checklist items done -> now Mark Complete succeeds
6. Confirm the vehicle's odometer updated to 5000 (Master Data -> Vehicles -> this vehicle)
7. Open the vehicle's detail page -> confirm the new Maintenance History tab shows this
   completed order with cost and vendor
8. Print the Maintenance Order -> confirm the checklist appears with check marks

Corrective test: create another Maintenance Order with category CORRECTIVE (no scope
template) -> Submit -> Start Work -> Mark Complete directly (no checklist gate) -> should
succeed immediately.

---

## Step 8 — Tire & Battery Transactions (Phase 3b)

1. Tire Transactions -> New: select tire, action MOUNT, vehicle, date, odometer
   -> confirm Tire Master (Master Data -> Tires) now shows status MOUNTED
2. New transaction: same tire, action DISMOUNT, no vehicle -> confirm status back to IN_STOCK
3. Battery Transactions -> same MOUNT/DISMOUNT flow, confirm Battery Master status syncs

---

## Step 9 — PM Due Detection (Phase 3b core logic)

This is best verified via the automated tests (pytest tests/unit/test_pm_due_calculation.py
and test_pm_auto_generation.py), but to see it live:

1. Edit a vehicle's current odometer (Master Data -> Vehicles -> Edit) to 4700 (300km from
   the 5000km PM schedule - inside the default 500km due-soon window)
2. From a Python shell: flask --app wsgi shell, then:
   ```python
   from app.core.maintenance.due_calculation_service import PMDueCalculationService
   from app.modules.master_data.vehicle.models import Vehicle
   v = Vehicle.query.first()
   PMDueCalculationService().get_due_status(v)
   # should show {'status': 'DUE_SOON', ...}
   ```
3. Run the auto-generation task manually:
   ```python
   from app.modules.transactions.maintenance_order.tasks import auto_generate_due_maintenance_orders
   auto_generate_due_maintenance_orders()
   ```
   Confirm a new DRAFT Maintenance Order was created, and (if you set up the
   pm_due_soon notification rule in Step 1g) the bell icon shows a new notification.

---

## Step 10 — Verify Cross-Cutting Features

**Audit Trail**: Sidebar -> Audit Trail -> filter by table vehicles - confirm every
create/update from the steps above is logged automatically (zero code needed for this).

**Permissions**: create a second user with no roles, log in, try any of the URLs above
directly - confirm a clean 403 page, not a crash.

**Notification bell**: click the bell icon in the top nav - confirm unread count badge
and dropdown work, and "Mark all read" clears the badge.

**Sidebar collapse**: click the chevron on "System Administration" or "Master Data" to
collapse/expand - refresh the page and confirm it remembers your last state.

**Dark mode**: toggle dark mode in the top nav - confirm it persists across page loads.

---

## Known Limitations at This Stage (by design, not bugs)

- Printing is browser print-to-PDF only - formal server-rendered PDF generation is Phase 5
- "Vehicles Due for Maintenance" dashboard widget UI is deferred to Phase 4
- Purchase Requests and Vehicle Registration (with LTO rules) are Phase 3c - not yet built
- Custom Fields, "My Actions" widget, and transaction comments are backlogged
  enhancements (see docs/superpowers/backlog.md)
