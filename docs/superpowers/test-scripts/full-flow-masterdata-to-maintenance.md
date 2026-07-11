# Full-Flow Test Data — Master Data -> Maintenance Transaction

This walks through one complete, realistic scenario end-to-end: setting up
a vehicle's PM plan from scratch and running it all the way through to a
completed Maintenance Order. Use this as your adjustment/retest script.

---

## Step 1 — Master Data (prerequisites, in this exact order)

**1a. Branch**
Sidebar -> Master Data -> Branches -> New
- Code: HQ
- Name: Head Office
- City: Manila

**1b. Vehicle Type**
Sidebar -> Master Data -> Vehicle Types -> New
- Code: SEDAN
- Name: Sedan
- Category: LIGHT

**1c. Maintenance Type** (the Category dropdown is now fixed - Preventive/Corrective/Predictive only)
Sidebar -> Master Data -> Maintenance Types -> New
- Code: PMS-010K
- Name: 10,000 KM PMS
- Category: Preventive
- Description: Standard 10,000 km preventive maintenance service

(Note: this screen no longer asks for Interval KM/Days - that's configured
per Make/Model in PM Templates, next.)

**1d. Vendor**
Sidebar -> Master Data -> Vendors -> New
- Code: HONDA-SVC
- Name: Honda Cars Service Center
- Type: Services

**1e. Vehicle**
Sidebar -> Master Data -> Vehicles -> New
- Conduction Number: HC-2024-001 (no plate yet)
- Vehicle Type: Sedan
- Brand: Honda
- Model: City
- Year: 2024
- Branch: Head Office (use the new searchable Branch field)
- Current Odometer: 9700 (deliberately close to the 10,000 km PM point)

---

## Step 2 — PM Configuration (Phase 3b + the Make/Model revision)

**2a. PM Template** (this is where the actual KM interval lives now)
Sidebar -> Transactions -> PM Templates -> New
- Make: Honda
- Model: City
- Maintenance Type: 10,000 KM PMS
- Trigger Mode: Hybrid (whichever comes first)
- Trigger KM: 10000
- Trigger Days: 365
- Priority: Medium
- Notify Before KM: 500 (or leave blank to use the system default)
- Notify Before Days: 30
- Escalate if Overdue: checked

**2b. PM Scope Template** (the checklist for this specific template)
Sidebar -> Transactions -> PM Scope Templates -> New
- Name: Honda City 10,000 KM PMS
- Maintenance Type: 10,000 KM PMS
- Ties to specific PM Template: Honda City - 10,000 KM PMS (select the one
  you just made - this is what keeps Honda's checklist separate from any
  other brand's)
- Activities:
  | Code | Description |
  |---|---|
  | OIL | Change Engine Oil |
  | FILTER | Replace Oil Filter |
  | BRAKE | Check Brake Pads |
  | TIRE | Check Tire Pressure |
  | BATTERY | Check Battery |

**2c. (Optional) Assign the template directly to the vehicle**
Master Data -> Vehicles -> edit the Honda City -> Assigned PM Template ->
select "Honda City - 10,000 KM PMS". This isn't required (Make/Model
auto-matching already finds it), but pins it explicitly so there's no
ambiguity if you later add another Honda City-specific template.

---

## Step 3 — System Administration (approval + numbering, if not already done)

- Document Types -> confirm MO (Maintenance Order) exists, Auto Numbering checked
- Numbering -> confirm a scheme exists for MO (prefix MO, year, 6 digits)
- If MO requires approval: Approval Path + Approval Matrix entries for MO

---

## Step 4 — Create the Maintenance Order (the transaction)

Sidebar -> Transactions -> Maintenance Orders -> New
- Vehicle: search HC-2024-001 or Honda City (use the search icon Advanced
  Search button to confirm the modal works - filter/sort/select)
- Maintenance Type: 10,000 KM PMS
- PM Scope Template: Honda City 10,000 KM PMS (generates the checklist)
- Scheduled Date: today
- Odometer at Service: 10000
- Assigned Mechanic: Juan Dela Cruz (free text)
- Vendor: search Honda Cars Service Center
- Estimated Cost: 3500

Save -> open the detail page -> confirm the 5 checklist items appear.

## Step 5 — Run the Order Through Its Lifecycle

1. Submit -> status PENDING (or APPROVED immediately if MO doesn't require approval)
2. Approve (if required)
3. Start Work -> status IN_PROGRESS
4. Try Mark Complete immediately -> should be blocked: "checklist item(s) still incomplete"
5. Go back and tick all 5 checklist items done
6. Mark Complete -> enter Actual Cost 3800, Completed Date today -> succeeds, status COMPLETED
7. Confirm the vehicle's odometer (Master Data -> Vehicles -> Honda City) is now 10000
8. Open the vehicle detail page -> confirm the Maintenance History tab shows
   this order with cost 3800.00 and vendor Honda Cars Service Center
9. Print the Maintenance Order -> confirm the checklist shows check marks
   and the letterhead pulls from Company Profile

## Step 6 — Verify PM Due-Detection Resets Correctly

From a Python shell (flask --app wsgi shell):
```python
from app.core.maintenance.due_calculation_service import PMDueCalculationService
from app.modules.master_data.vehicle.models import Vehicle
v = Vehicle.query.filter_by(conduction_number="HC-2024-001").first()
PMDueCalculationService().get_due_status(v)
# Expect: {'status': 'GOOD', 'next_due_km': 20000, ...} - the clock reset
# from the completed order's odometer (10000) + the template's interval (10000)
```

---

## Sidebar Fixes to Verify While You're In There

- Master Data -> Vehicle Types - should highlight only "Vehicle Types," not also "Vehicles"
- Transactions -> Tire Transactions / Battery Transactions - should not also
  highlight "Tires" / "Batteries" under Master Data
- Transactions -> Vehicle Movement - should not also highlight "Vehicles"
  under Master Data

## Known Remaining Scope

Approval Paths, Approval Matrix, and PM Templates' own Vehicle Type/Maintenance
Type dropdowns are intentionally left as plain searchable dropdowns (not AJAX
or Search Modal) since these lists realistically stay under ~20-30 records for
any single client. If your actual usage grows past that, tell me and I'll
convert them the same way as Vehicles/Branches.
