# Lookup Type Reference — Which Module Uses Which Lookup

This is the master list of `lookup_type` codes used across the system's
generic Lookup Maintenance screen (System Administration → Lookups).
When adding lookup values, use the **exact** `lookup_type` string shown
below (case-sensitive, uppercase with underscores).

## Currently wired to a dropdown (Lookup-driven today)

| lookup_type | Used by | Example codes seeded | Where it appears |
|---|---|---|---|
| `FUEL_TYPE` | Vehicle Master | DIESEL, GASOLINE, ELECTRIC, HYBRID, LPG | Vehicle form → Fuel Type dropdown |
| `LICENSE_TYPE` | Driver Master | PROFESSIONAL, NON_PROFESSIONAL, STUDENT_PERMIT | Driver form → License Type dropdown |

These two are seeded automatically by `flask seed all` (via the module's own
`LookupRegistry.register()` calls) so they're ready out of the box; you can
add more values to them anytime in Lookup Maintenance without touching code.

## Fields that are currently free-text / fixed choice (not yet Lookup-driven)

These fields exist and work today, but their allowed values are either a
small hardcoded set in code or a free-text field — not yet wired to the
generic Lookup screen. If you want any of these to become admin-editable via
Lookup Maintenance instead, let me know and I'll wire it (small change per
field, same pattern as FUEL_TYPE/LICENSE_TYPE above).

| Field | Model | Current values | Notes |
|---|---|---|---|
| `category` (Vehicle Type) | VehicleType | LIGHT, HEAVY, MOTORCYCLE, SPECIAL (free text today) | Comment in code already says "from Lookup VEHICLE_CATEGORY" — planned but not wired yet |
| `category` (Maintenance Type) | MaintenanceType | PREVENTIVE, CORRECTIVE (free text today) | Drives Maintenance Order's checklist-required logic |
| `tire_type` | Tire | RADIAL, BIAS (free text today) | |
| `vendor_type` | Vendor | GOODS, SERVICES, BOTH (fixed dropdown in form, not Lookup-driven) | |
| `movement_type` | VehicleMovement | TRANSFER, DISPATCH, RETURN, OTHER (fixed dropdown, validated in code) | |
| `action` (Tire Transaction) | TireTransaction | MOUNT, DISMOUNT, RETREAD, DISPOSE (fixed, validated in code) | |
| `action` (Battery Transaction) | BatteryTransaction | MOUNT, DISMOUNT, DISPOSE (fixed, validated in code) | |
| `trigger_mode` (PM Schedule) | PMSchedule | KM, CALENDAR, HYBRID (fixed dropdown) | |
| `priority` (PM Schedule) | PMSchedule | LOW, MEDIUM, HIGH (fixed dropdown) | |
| `status` fields (all transaction modules) | various | DRAFT/PENDING/APPROVED/etc. | These are workflow states, intentionally not Lookup-driven — they're controlled by the Approval Engine and physical lifecycle logic, not arbitrary admin-editable text |

## How to add a new Lookup value to an existing type

1. Sidebar → **Lookups**
2. Click **New Lookup**
3. Lookup Type: enter the exact code from the table above (e.g. `FUEL_TYPE`)
4. Code: your new value's short code (e.g. `CNG`)
5. Description: display label (e.g. "Compressed Natural Gas")
6. Sort Order: controls dropdown ordering
7. Save — it appears in the relevant dropdown immediately, no restart needed

## How to request a new Lookup-driven field

If you want one of the "not yet Lookup-driven" fields above converted to
use the Lookup Maintenance screen (so your admin can add values without
asking me), just name the field — it's a small, consistent change following
the same pattern already used for FUEL_TYPE and LICENSE_TYPE.
