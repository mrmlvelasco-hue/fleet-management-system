# Lookup Type Reference — Which Module Uses Which Lookup

This is the master list of `lookup_type` codes used across the system's
generic Lookup Maintenance screen (System Administration -> Lookups).
When adding lookup values, use the exact `lookup_type` string shown
below (case-sensitive, uppercase with underscores).

## Fully Lookup-driven today (admin can add values, no code change needed)

| lookup_type | Used by | Seeded defaults | Where it appears |
|---|---|---|---|
| FUEL_TYPE | Vehicle Master | DIESEL, GASOLINE, ELECTRIC, HYBRID, LPG | Vehicle form -> Fuel Type |
| LICENSE_TYPE | Driver Master | PROFESSIONAL, NON_PROFESSIONAL, STUDENT_PERMIT | Driver form -> License Type |
| VEHICLE_CATEGORY | Vehicle Type master | LIGHT, HEAVY, MOTORCYCLE, SPECIAL | Vehicle Type form -> Category |
| VENDOR_TYPE | Vendor master | GOODS, SERVICES, BOTH | Vendor form -> Vendor Type |
| TIRE_TYPE | Tire master | RADIAL, BIAS | Tire form -> Type |
| MOVEMENT_TYPE | Vehicle Movement transaction | TRANSFER, DISPATCH, RETURN, OTHER | Movement form -> Movement Type (also validated server-side against this Lookup) |
| PM_PRIORITY | PM Schedule | LOW, MEDIUM, HIGH | PM Schedule form -> Priority |

All seven are seeded automatically by `flask seed all`. Before the first
seed run (fresh install), the dropdowns still show the code-registered
defaults automatically (a safe fallback), so the UI never renders an empty
dropdown even pre-seed.

To add a new value to any of the above: Sidebar -> Lookups -> New Lookup ->
enter the exact lookup_type code from the table, your new code and
description, save. It appears in the dropdown immediately.

## Intentionally NOT Lookup-driven (business logic depends on the exact value)

These fields have real if/elif code branches keyed to their specific
values -- adding a new value via Lookup Maintenance alone would not give it
matching behavior, so they stay as fixed dropdowns until a corresponding
code change is made:

| Field | Model | Fixed values | Why it can't be freely added-to |
|---|---|---|---|
| category (Maintenance Type) | MaintenanceType | PREVENTIVE, CORRECTIVE | Determines whether a Maintenance Order requires a completed checklist before it can be marked complete |
| trigger_mode | PMSchedule | KM, CALENDAR, HYBRID | Determines which due-date/due-km math the PMDueCalculationService runs |
| action (Tire Transaction) | TireTransaction | MOUNT, DISMOUNT, RETREAD, DISPOSE | Each value drives a specific Tire Master status-sync rule |
| action (Battery Transaction) | BatteryTransaction | MOUNT, DISMOUNT, DISPOSE | Same as above, for Battery Master |
| status fields (all transaction modules) | various | DRAFT/PENDING/APPROVED/etc. | Controlled entirely by the Approval Engine and each module's physical-lifecycle logic |

If you need a new value in one of these (e.g. a 4th Tire Transaction action
like "REPAIR"), tell me -- it's a small, well-understood change (add the enum
value + its corresponding status-sync branch), just not a pure Lookup-Maintenance-only edit.

## How to request more fields converted to Lookup-driven

Any other free-text or hardcoded-dropdown field you'd like made
admin-configurable -- just name it. Converting one follows the same pattern
used above: register defaults in code, add a DB-first-with-fallback query,
swap the form's input/hardcoded select for a Lookup-driven one.
