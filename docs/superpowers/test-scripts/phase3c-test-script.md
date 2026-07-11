# Phase 3c — Manual Test Script (Purchase Requests, Vehicle Registration)

Prerequisites: Phase 1–3b already seeded and working. Log in as an account
with the System Administrator role.

## 1. Purchase Request — amount-driven approval routing

Setup (do once):
1. System Administration → Approval Paths → create two paths:
   - "Small PR Approval" — Level 1: Role = e.g. Supervisor
   - "Large PR Approval" — Level 1: Role = e.g. Finance Manager
2. System Administration → Approval Matrix → create two entries for
   Document Type = PR:
   - Path = Small PR Approval, Min Amount = 0, Max Amount = 10000
   - Path = Large PR Approval, Min Amount = 10000.01, Max Amount = (blank)

Test:
1. Sidebar → **Purchase Requests** → New Request
   - Description: "Office Supplies"
   - Add 2 line items (e.g. Paper: qty 10 @ 50, Pens: qty 20 @ 15)
   - Save → confirm the Amount shown on the detail page = 800 (10×50 + 20×15)
2. Submit the request → confirm it routes to the **Small PR Approval** path
   (only a Supervisor-role user can approve)
3. Log in as a user with the Supervisor role (or add the role to your test
   user) → Approve → confirm status becomes APPROVED
4. Create a **second** PR with a line item costing e.g. 50,000 → Submit →
   confirm a Supervisor-role user gets a permission/eligibility error when
   trying to approve (should NOT be eligible) — only a Finance-Manager-role
   user can approve this one
5. On an approved PR: click **Mark Ordered**, then **Mark Received** →
   confirm status transitions correctly
6. **Print** the PR → confirm the line items and total appear correctly in
   the printable view

## 2. Vehicle Registration — LTO rules

1. Sidebar → **Vehicle Registration** → New Registration
   - Vehicle: pick a test vehicle that currently has only a Conduction Number
     (no plate yet)
   - Registration Type: `New`
   - Registration Date: today
   - Save → open the record, confirm **Validity** shows "3 year(s)" and
     **Expiry Date** = today + 3 years
2. Submit the registration (auto-approves if VR doc type doesn't require
   approval, or approve it if it does)
3. On the detail page, use the **Complete Registration** form:
   - Enter an OR Number and CR Number
   - Enter a Plate Number (e.g. "ABC 1234")
   - Submit → confirm status becomes COMPLETED
4. Go to Master Data → Vehicles → open that same vehicle → confirm the
   **Plate Number** field is now populated with what you entered (this is
   the Conduction Number → Plate Number transition from the LTO rule)
5. Scroll down on the vehicle detail page → confirm a **Registration
   History** card shows this registration
6. **Negative test**: try creating a second `New` registration for the same
   vehicle while the first one is still active → should be rejected with an
   error message
7. Create a `Renewal` registration for that vehicle → confirm **Validity**
   defaults to "1 year(s)" this time (LTO renewal rule, different from the
   3-year initial registration)
8. **Print** the registration → confirm OR/CR/Plate numbers appear correctly

## 3. Permission Enforcement

1. Create a plain user with no roles → confirm `/transactions/purchase-requests`
   and `/transactions/vehicle-registrations` both return **403 Forbidden**
2. Grant only `purchaserequest.view` → confirm they can see the list but
   have no "New Request" button and no action buttons on the detail page

## 4. Automatic behaviors to spot-check

- Audit Trail: filter by table = `purchase_requests` or
  `vehicle_registrations`, confirm every create/update was logged
  automatically
- If Notification Rules are configured for `submitted`/`approved_final`
  events, confirm the bell icon shows a notification when a PR or VR moves
  through those states

## 3. PM Template Revision — Make/Model-specific intervals (client feedback)

Setup:
1. Master Data → Maintenance Types → create `PMS-010K` (category PREVENTIVE)
2. System Administration → PM Templates → New:
   - Make: `Honda`, Model: `City`
   - Maintenance Type: PMS-010K, Trigger: KM, Interval KM: 10,000
   - Notify Before KM: 500
3. PM Templates → New (second one, same maintenance type, different brand):
   - Make: `Toyota`, Model: `Vios`
   - Trigger: KM, Interval KM: 8,000

Test:
1. Master Data → Vehicles → create a Honda City and a Toyota Vios, both with
   odometer readings close to their respective due points
2. Confirm each vehicle's due status calculates independently against its
   own Make/Model template, not a shared generic interval
3. Optionally: edit the Honda City vehicle and set **Assigned PM Template**
   explicitly — confirm this direct assignment takes precedence even if you
   later add a different Make/Model schedule
4. PM Scope Templates → New → link a scope specifically to the "Honda City"
   PM Template (not just the generic Maintenance Type) — confirm a Toyota
   Vios does NOT pick up this Honda-specific checklist when its own
   Maintenance Order is generated
5. CSV import: re-upload the updated `pm_schedules_template.csv` /
   `pm_scope_items_template.csv` (now with vehicle_make/vehicle_model
   columns) and confirm they import correctly with make/model preserved
