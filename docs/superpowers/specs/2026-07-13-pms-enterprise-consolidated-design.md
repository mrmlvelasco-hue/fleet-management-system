# PMS Enterprise Enhancement — Consolidated Design Spec (PMS-1 through PMS-6)

**Date:** 2026-07-13
**Status:** Approved — proceeding in this order, Option B confirmed as new
default Next-PMS-Generation behavior.

## PMS-1 — Data Model Overhaul

**PMSchedule** additions (all nullable, fully backward compatible with
existing rows):
- `vehicle_brand_id` (FK vehicle_brands.id) / `vehicle_model_id` (FK
  vehicle_models.id) — the *real* FK-based match, preferred over the
  existing free-text `vehicle_make`/`vehicle_model` columns when set.
  Free-text columns are kept as-is for backward compatibility and as a
  fallback when no FK is set (avoids a risky data migration of existing
  PM Template rows).
- `variant` (String, nullable) — e.g. engine/trim variant like "2.8 D-4D"
- `engine_type` (String, nullable)
- `fuel_type` (String, nullable) — matches the Vehicle master's
  FUEL_TYPE lookup values
- `transmission` (String, nullable)
- `model_year_from` / `model_year_to` (Integer, nullable) — inclusive
  range; NULL on either side = unbounded
- `profile_code` (String, unique, nullable) — the human-facing PMS
  Profile identifier (e.g. "HILUX-DIESEL")
- `profile_description` (String, nullable)
- `effective_date` (Date, nullable)

**Matching precedence in PMDueCalculationService** (extends the existing
precedence chain): assigned template → exact FK Brand+Model match (+
variant/engine/fuel/transmission/year-range as tie-breakers when multiple
FK-matched schedules exist for the same vehicle) → free-text Make/Model
match (existing) → Vehicle Type → global.

**Vehicle master** additions: `variant`, `engine_type`, `transmission`
(all String, nullable), `current_engine_hours` (Integer, nullable).

## PMS-2 — PMS Policy Master

New `PMPolicy` entity: name, description, trigger_method (KM | CALENDAR |
ENGINE_HOURS | DAYS | WHICHEVER_FIRST | WHICHEVER_LAST), is_active.
`PMSchedule` gets an optional `pm_policy_id` FK — when set, the policy's
trigger_method takes precedence over the schedule's own `trigger_mode`
(backward compatible: schedules without a policy keep working exactly as
today). Vehicle gets an optional `pm_policy_id` override, same pattern as
`pm_schedule_id`.

Engine Hours support: `PMSchedule.interval_engine_hours` (Integer,
nullable), matched against `Vehicle.current_engine_hours`.
WHICHEVER_LAST: due only once *all* configured intervals (km/days/hours)
have elapsed, inverse of HYBRID's whichever-first logic.

## PMS-3 — Generation Policy + Due-Calculation Method

`DocumentType` (or a new `PMPolicy` field) gets `next_pms_generation` =
MANUAL | AUTO_SCHEDULE | AUTO_MO, default **AUTO_SCHEDULE (Option B)** —
this is the behavior change: the existing auto-generation task currently
always creates a Maintenance Order directly (Option C-like); it will
instead default to creating the *next PMSchedule occurrence* only
(tracked via a lightweight "next due" pointer), status effectively
Pending, with no Maintenance Order until a user acts. AUTO_MO preserves
today's literal behavior for anyone who explicitly wants it back.

`next_due_calculation_method` = ACTUAL_COMPLETION | ORIGINAL_SCHEDULE |
ADMIN_CHOICE, default **ACTUAL_COMPLETION** (matches current hardcoded
behavior — already correct, just needs to become a real configurable
field rather than implicit).

## PMS-4 — Warning Tiers + Vehicle Status Lifecycle

`PMPolicy` gets a second threshold tier: `critical_before_km` /
`critical_before_days` (in addition to the existing warning-tier
`notify_before_km`/`notify_before_days` already on PMSchedule), plus
`overdue_grace_days`. Due-status calculation gains a 4th state:
GOOD → DUE_SOON → OVERDUE → CRITICAL (or grace-period-aware OVERDUE).

Vehicle status lifecycle: new intermediate states (SCHEDULED_FOR_PMS,
UNDER_MAINTENANCE, WAITING_FOR_PARTS, RELEASED, OUT_OF_SERVICE) alongside
existing ACTIVE/INACTIVE/IN_REPAIR/DISPOSED, auto-updated by
MaintenanceOrderService's lifecycle hooks (submit/start_work/complete).

## PMS-5 — Enhanced Activities + Parts Requirement

`PMScopeItem` additions: `required_fluids` (Text, separate from
`required_parts`), `inspection_required` (Boolean), `is_mandatory`
(Boolean, default True), `estimated_duration_minutes` (Integer).

New `PMScopeItemPart`: scope_item_id (FK), part_number, part_name,
quantity, unit_of_measure, is_required (Boolean). Lightweight — no full
Parts/Inventory Master yet (that's a future Inventory module
prerequisite per the source doc's own note); Part Number/Name stay as
plain fields on this table for now.

`MaintenanceOrderService.create()` — when generated from a PM Schedule
with a linked scope template, auto-populates a parts-requirement list on
the Maintenance Order from `PMScopeItemPart` rows (read-only reference
list on the MO, informational for now — full parts-consumption/inventory
integration is future work).

## PMS-6 — Pool Vehicles + Scheduler Dashboard

Vehicle gets `assignment_type` = PERMANENT | POOL | DEPARTMENT | BRANCH.
Pool vehicles: Trip Ticket temporarily links driver/employee without
changing `Vehicle.assigned_driver_id` permanently.

Dashboard: replace the single Maintenance KPI card with a breakdown —
Due Today, Due This Week, Overdue, Upcoming (KM threshold), Upcoming
(Days threshold), Under Maintenance, Waiting for Approval, Waiting for
Parts — each a mini stat sourced from PMDueCalculationService +
MaintenanceOrder status counts, org-scope aware like every other widget.

## Testing
Each sub-phase gets full TDD coverage per the established project
convention — unit tests for the new matching/policy logic, integration
tests through real routes for any new admin UI, and explicit backward-
compatibility tests proving existing PM Templates/schedules/vehicles
continue to behave identically when the new optional fields are left
unset.
