# Phase 4 — Dashboard — Design Spec

**Date:** 2026-07-13
**Status:** Approved

## What Already Exists
- `DashboardWidget` + `UserDashboardConfig` (Phase 1c) — the configurability
  infrastructure ("Dashboard widgets shall be configurable" per the master
  prompt) is already built and seeded with exactly 6 widget codes: FLEET,
  MAINTENANCE, APPROVALS, REGISTRATIONS, TIRES, BATTERIES.
- The dashboard template already renders these 6 as placeholder cards
  showing "—".
- "For My Action" widget (built ad-hoc during F2/F3) already shows the
  personal pending-approval list.
- `PMDueCalculationService.get_all_due_vehicles()` — already computes
  GOOD/DUE_SOON/OVERDUE per vehicle (Phase 3b).
- `VehicleRegistrationService.get_expiring_registrations(days_ahead=30)` —
  already exists (Phase 3c).

Phase 4's job is mostly **wiring real numbers into infrastructure that
already exists**, not building new infrastructure from scratch.

## KPI Cards (the 6 existing widget slots)
- **FLEET**: total active vehicle count
- **MAINTENANCE**: count of vehicles currently DUE_SOON or OVERDUE
  (via PMDueCalculationService)
- **APPROVALS**: count of the current user's pending ApprovalTasks
  (reuses ApprovalTaskService.list_for_user())
- **REGISTRATIONS**: count of registrations expiring within 30 days
- **TIRES**: count of tires currently IN_STOCK (available spares)
- **BATTERIES**: count of batteries currently IN_STOCK

All counts are **org-scope aware** — a Manila-scoped user sees Manila's
fleet count, not company-wide, consistent with the view-scope work already
done. This requires each underlying service's `list()`/count method to
accept the same `user=` parameter already used elsewhere.

## Configurability
Respect `UserDashboardConfig`: a widget is shown unless the user has an
explicit `is_visible=False` row for that widget code (falls back to
`DashboardWidget.default_visible` if no per-user config exists — same
"unconfigured = default" pattern used throughout this project). Add a
small "Customize Dashboard" toggle screen (checkboxes per widget) so users
can hide cards they don't need.

## New Widget: "Vehicles Due for Maintenance"
Table below the KPI cards — vehicle, plate/conduction number, schedule
matched, status (DUE_SOON/OVERDUE badge), next due km/date. Sourced from
`PMDueCalculationService.get_all_due_vehicles()`, filtered by the viewer's
org scope. This was explicitly deferred here from the Phase 3b spec.

## Clicking Through
Each KPI card links to its underlying list screen (Fleet → Vehicles,
Maintenance → Maintenance Orders, Approvals → dashboard's own "For My
Action" section, Registrations → Vehicle Registrations, Tires → Tires,
Batteries → Batteries) — consistent with the existing "For My Action"
click-through pattern.

## Out of Scope (this pass)
"Documents Under Review" / "Comments Trend" style widgets from the earlier
reference screenshot — those need the unified approval view / discussion
thread (F4) as a prerequisite, not yet built. Full report/export
functionality is Phase 5.

## Testing
Unit: each KPI count method respects org-scope and the include/exclude
active filters. Widget visibility respects UserDashboardConfig with
correct default-fallback. Integration: dashboard renders real numbers (not
placeholders), a scoped user sees scoped counts, hiding a widget via config
removes it from the rendered page, the due-vehicles table renders and
click-through links resolve correctly.
