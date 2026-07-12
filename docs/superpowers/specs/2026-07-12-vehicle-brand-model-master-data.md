# Vehicle Brand/Model Master Data — Design Spec

**Date:** 2026-07-12
**Status:** Approved (client feedback)

## Problem
Vehicle.brand/Vehicle.model are free-text strings, allowing spelling
variations ("Toyota" vs "toyota" vs "Toyata"), duplicates, and inconsistent
data — critical because PM Templates match against these exact strings for
maintenance planning.

## Design Decision: validated strings, not a full FK migration
Converting Vehicle.brand/model to FK columns would touch ~15+ call sites
(templates, search services, PM matching, CSV import, ~40+ existing tests)
that read `.brand`/`.model` as plain strings. Instead:

- New master tables **VehicleBrand** (name, unique) and **VehicleModel**
  (brand_id FK + name, unique per brand) act as the "list of valid values."
- Vehicle.brand/Vehicle.model **stay as String columns** (zero blast radius
  on existing code/templates/PM matching/tests).
- The **web form** enforces selection from the master list via cascading
  dropdowns (select Brand → Model list filtered to that brand) — physically
  prevents free typing.
- The **service layer** validates brand/model against the master list with
  friendly errors, but stays lenient (get-or-create) for programmatic/CSV
  callers so the existing test suite and CSV import aren't broken.

## Data Models
**VehicleBrand**: name (unique, required), is_active.
**VehicleModel**: brand_id (FK), name, unique(brand_id, name), is_active.

## Validation & Friendly Error Messages (exact messages requested)
- "Brand is required."
- "Model is required."
- "Please select a valid Brand from the master list."
- "Please select a valid Model from the master list."
- "Selected Model does not belong to the selected Brand."
- "Brand already exists in the master data." (on VehicleBrand create)
- "Model already exists for the selected Brand." (on VehicleModel create)

## UI
- New System Administration screens: Vehicle Brands, Vehicle Models (CRUD,
  same list/form pattern as other masters).
- Vehicle form: Brand select (AJAX/plain, populated from VehicleBrand) +
  Model select cascading from the chosen Brand (AJAX endpoint
  `/api/search/vehicle-models?brand_id=X`).
- Vehicle Registration doesn't touch Brand/Model directly (reads from the
  linked Vehicle), so no changes needed there beyond the date fix already
  shipped.

## Date Validation (already shipped separately)
`parse_form_date`/`parse_form_datetime` now used in all 16 form date
parsing call sites — see the date validation commit.
