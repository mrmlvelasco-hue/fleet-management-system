# Phase 2 — Master Data Modules — Design Spec

**Date:** 2026-07-10
**Status:** Approved
**Phase:** 2 — Master Data (builds on Phase 1a/1b/1c)

## Sub-phases
- 2a: Branch, Department, BusinessUnit, VehicleType, MaintenanceType
- 2b: Attachment (core/shared), Vehicle, Driver, Tire, Battery, Vendor

## Cross-cutting rules
- Hard deletes blocked; deactivate/reactivate only (complete history)
- Automatic audit trail via 1a flush listeners (zero extra code)
- Generic Attachment model in core/ reused by all masters and Phase 3 transactions
- All lookup dropdowns (fuel type, vehicle category, etc.) from Lookup table (1c)
- FK to Branch wired on User model (branch_id was nullable placeholder in 1a)

## Models: 2a
Branch, Department, BusinessUnit, VehicleType, MaintenanceType — see design doc.

## Models: 2b
Attachment (core), Vehicle, Driver, Tire, Battery, Vendor — see design doc.

## Permissions
vehicle.view/create/update/delete, driver.view/create/update/delete,
tire.view/create/update/delete, battery.view/create/update/delete,
vendor.view/create/update/delete, branch.view/create/update/delete,
department.view/create/update/delete, businessunit.view/create/update/delete,
vehicletype.view/create/update/delete, maintenancetype.view/create/update/delete,
attachment.upload/delete
