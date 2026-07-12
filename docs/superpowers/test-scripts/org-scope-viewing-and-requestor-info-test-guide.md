# Test Guide — Organizational Scope Viewing Restriction + Requestor Information

## Part 1 — Requestor Information (all 8 transaction modules)

1. Create any transaction (e.g. Trip Ticket) as a test user with Employee
   ID, Branch, and Department set on their own User profile
   (Sidebar → Users → edit your test user → fill in Employee ID/Branch/
   Department first)
2. Open the transaction's detail page → scroll down → confirm the
   **Requestor Information** card shows: Requested By (full name),
   Employee ID, Department, Branch, Date Created, Last Updated, Last
   Updated By
3. Edit the transaction (any status-changing action) as a *different* user
   → reload the detail page → confirm **Last Updated By** now shows the
   second user, while **Requested By** still shows the original creator
4. Go to that module's list page → confirm a **Requested By** column
   appears → type part of a requester's name into the DataTables search
   box at the top of the list → confirm it filters correctly (this uses
   the existing DataTables integration, no new search UI needed)

Repeat for a couple of other modules (Maintenance Order, Purchase Request)
to confirm the same panel appears consistently.

## Part 2 — Organizational Scope for Viewing (not just approving)

**Setup:**
1. Create two branches: "Manila" and "Cebu"
2. Create two users, e.g. "ana" and "ben", both with `tripticket.view`
3. Assign "ana" → Organizational Scope → Branch → Manila
   (leave "ben" with no scope assigned at all)

**Test A — Scoped user sees only their branch:**
1. Create a vehicle in Manila, submit a Trip Ticket for it (as any third
   user)
2. Create a vehicle in Cebu, submit another Trip Ticket for it
3. Log in as "ana" → Trip Tickets list → confirm only the Manila trip
   appears, not the Cebu one
4. Try navigating directly to the Cebu trip's URL
   (`/transactions/trip-tickets/<id>`) → confirm you get a **403 Forbidden**
   page, not just a missing list row — this is genuine access control, not
   cosmetic hiding

**Test B — Unscoped user (backward compatibility):**
1. Log in as "ben" (no scope assigned) → Trip Tickets list → confirm
   **both** the Manila and Cebu trips appear
2. This confirms the rollout-safety rule: a user isn't restricted until an
   admin explicitly assigns them a scope

**Test C — Self-visibility exception:**
1. As "ana" (Manila-scoped), submit a Trip Ticket using a Cebu vehicle
   (if your permissions allow creating one)
2. Confirm "ana" can still see and open her own submission afterward, even
   though it's a Cebu-branch record — the requester can always see their
   own transactions

**Test D — Global/Company scope bypasses restriction entirely:**
1. Assign a third user "carla" → Organizational Scope → Global
2. Confirm "carla" sees every transaction across every branch, in every
   module

Repeat Test A/B against a couple of other modules (Maintenance Order,
Purchase Request) to confirm consistent behavior — this is wired into
all 8 transaction modules identically via the shared
`BaseTransactionService`.

## Known Gaps / Follow-ups

- Action routes (Submit/Approve/Reject/Return/Cancel/etc.) are not yet
  individually gated by view-scope — they're currently only reachable via
  the detail page (which *is* gated), but a technically savvy user could
  still POST directly to an action URL for an out-of-scope record. Flag
  if you want this hardened further.
- Business-Unit-based scoping works in the engine but no module currently
  supplies `business_unit_id` — only `branch_id` is auto-inferred today.
- Reports/exports are not yet built (Phase 5) so "include in reports" from
  the Requestor Info request isn't applicable yet.
