# Phase 3b Revision — PM Templates by Make/Model

**Date:** 2026-07-11
**Status:** Approved (client feedback)

## Problem
PMSchedule matched only by generic VehicleType (e.g. "Light Vehicle"), so
different brands/models sharing a type got the same PM interval — wrong,
since every manufacturer publishes different KM/month intervals.

## Revised Design

**PMSchedule** ("PM Template" in UI) — new fields:
- `vehicle_make`, `vehicle_model` (String, nullable) — matched case-insensitively
  against Vehicle.brand/Vehicle.model. Takes precedence over vehicle_type_id.
- `notify_before_km`, `notify_before_days` (Integer, nullable) — per-template
  overrides of the global PM_DUE_SOON_KM/PM_DUE_SOON_DAYS system parameters.
- `escalate_if_overdue` (Boolean, default True) — when true, OVERDUE fires
  pm_overdue notifications (existing mechanism); false suppresses escalation
  for low-priority equipment.

**Vehicle** — new field:
- `pm_schedule_id` (FK PMSchedule, nullable) — "Assigned PM Template" set at
  registration/edit time. Direct assignment always wins over any matching.

**PMScopeTemplate** — new field:
- `pm_schedule_id` (FK PMSchedule, nullable) — ties a scope/checklist to one
  specific template (e.g. "Honda City 10,000 KM PMS" vs "Toyota Hilux
  10,000 KM PMS" both tagged maintenance_type PMS-010K but different scopes).
  maintenance_type_id-only templates remain supported for backward
  compatibility / generic fallback.

## Matching precedence (PMDueCalculationService)
1. Vehicle.pm_schedule_id (direct assignment) — if set, use only that schedule
2. Exact vehicle_make + vehicle_model match (case-insensitive)
3. vehicle_type_id match (existing behavior)
4. Global schedule (vehicle_type_id AND vehicle_make/model all NULL)

## Alert thresholds
due_soon_km = schedule.notify_before_km or SystemParameter PM_DUE_SOON_KM
due_soon_days = schedule.notify_before_days or SystemParameter PM_DUE_SOON_DAYS

## Scope resolution for auto-generated Maintenance Orders
Prefer a PMScopeTemplate directly linked to the matched schedule
(pm_schedule_id); fall back to maintenance_type_id match if none.

## CSV import
Extend PM Schedule import columns with vehicle_make, vehicle_model,
notify_before_km, notify_before_days, escalate_if_overdue (optional
columns, backward-compatible with existing template files).

## Out of scope
Year-model-specific intervals (facelift-year variance) — not requested,
can be added later as an additional optional match key if needed.
