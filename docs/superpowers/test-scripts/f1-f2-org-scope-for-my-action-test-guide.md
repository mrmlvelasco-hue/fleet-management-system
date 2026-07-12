# Test Guide — F1 (Org-Scoped Approval) + F2 (For My Action Widget)

## Setup

1. Create two branches: Master Data → Branches → "Manila" and "Cebu"
2. Create a role "Fleet Manager" (Sidebar → Roles → New) with whatever
   permissions you need for the module you'll test with (e.g. Trip Ticket)
3. Create two users, both with the Fleet Manager role:
   - "juan" — will be scoped to Manila only
   - "pedro" — will be scoped to Cebu only
4. Sidebar → Users → click the new 🔀 icon (next to Edit) for "juan" →
   Organizational Scope → Add Scope → Type: Branch, Branch: Manila
5. Same for "pedro" → Branch: Cebu

## Test 1 — Correct branch routes to the correct approver

1. Create a vehicle in Manila branch, a Trip Ticket for it, submit it
   (using a third user, e.g. a regular dispatcher)
2. Log in as "pedro" (Cebu-scoped) → Dashboard → "For My Action" — the
   Manila Trip Ticket should **NOT** appear
3. Log in as "juan" (Manila-scoped) → Dashboard → "For My Action" — the
   Trip Ticket **should** appear, clickable, showing document number,
   type, requester, and waiting time
4. Click it → lands on the actual Trip Ticket detail page → Approve button
   works normally (existing permission/eligibility checks still apply)

## Test 2 — Global/Company scope

1. Create a third user "maria" with the Fleet Manager role, Org Scope →
   Global
2. Submit another Trip Ticket from either branch
3. "maria" should see it in her For My Action list regardless of branch

## Test 3 — Backward compatibility (no scope assigned)

1. Create a user "carlos" with the Fleet Manager role but **don't** assign
   any Organizational Scope at all
2. Submit a Trip Ticket from Manila
3. "carlos" should still be able to see and approve it — a user with zero
   scope rows is treated as unrestricted (this is intentional: rolling out
   org-scoping must never silently lock out approvers who haven't been
   explicitly configured yet)

## Test 4 — For My Action widget details

- Badge count on the widget matches the number of pending items
- Empty state: log in as a user with no pending tasks → "Nothing waiting
  for your action right now."
- Aging display: a task submitted a few minutes ago shows "X hour(s)
  waiting" or "Just now"; older ones show "X day(s) waiting"

## Known Gaps (deferred to F4+)

- No "Documents Under Review" / "Comments Trend" widgets yet (those track
  a broader "all documents in my org" view, separate from personal
  pending-actions)
- No unified approval view yet — clicking a task goes to that module's own
  existing detail page, which already has Approve/Reject/Return buttons
- No discussion/comment thread yet
- No "Request Additional Information" action yet (only Approve/Reject/
  Return exist currently)
- Business-unit scoping is implemented but no module currently supplies
  `business_unit_id` at submit time (only `branch_id` is auto-inferred) —
  tell me if you need a specific module to route by Business Unit instead
  of/in addition to Branch
