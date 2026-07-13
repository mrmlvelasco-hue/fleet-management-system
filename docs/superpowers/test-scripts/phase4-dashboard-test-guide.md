# Test Guide — Phase 4 Dashboard

## Setup
Ensure `flask seed all` has been run (seeds the 6 dashboard widgets:
FLEET, MAINTENANCE, APPROVALS, REGISTRATIONS, TIRES, BATTERIES).

## Step 1 — Real KPI values
1. Log in → Dashboard — confirm all 6 cards show real numbers, not "—"
2. Fleet count matches Master Data → Vehicles (active count)
3. Tires/Batteries counts match the IN_STOCK count on their respective
   master lists
4. Click any KPI card → confirm it navigates to the matching module (e.g.
   Fleet → Vehicles list, Tires → Tires list)

## Step 2 — Org-scope awareness
1. Using the Manila/Cebu setup from earlier testing, log in as a
   Manila-scoped user → confirm Fleet/Tires/Batteries counts reflect only
   Manila's data, not company-wide
2. Log in as an unscoped user (or admin) → confirm counts reflect
   everything, unrestricted

## Step 3 — Configurability ("Customize" button)
1. Click "Customize" (top-right of Dashboard) → this is the existing
   Dashboard Configuration screen from Phase 1c
2. Uncheck a widget (e.g. Batteries) → Save
3. Return to Dashboard → confirm that card no longer appears
4. Re-check it → confirm it reappears

## Step 4 — "For My Action" widget
Already covered by earlier F2/F3 testing — still present and unchanged.

## Step 5 — "Vehicles Due for Maintenance" widget
1. Ensure at least one vehicle is DUE_SOON or OVERDUE per its PM Template
   (see the earlier PM Scheduling test guide for setup)
2. Dashboard → confirm the vehicle appears in this table with the correct
   status badge, next-due KM/date
3. Click the vehicle name → confirm it navigates to that vehicle's detail
   page
4. Confirm this table also respects org-scope (a Cebu-scoped user
   shouldn't see a Manila vehicle's due-maintenance row)

## Known Gaps (deferred)
- "Documents Under Review" / "Comments Trend" widgets from the original
  WebAXIS-style reference screenshot — need the unified approval view (F4)
  as a prerequisite, not yet built
- No charts/graphs yet, only numeric KPI cards and one table widget
- Full report/export functionality is Phase 5
