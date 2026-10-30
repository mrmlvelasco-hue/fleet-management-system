# Branch → Company UI Cutover — Design

**Date:** 2026-10-30 · **Stage:** 3C/3D continuation · For client delivery.

Michael's request, with decisions confirmed 2026-10-30.

---

## 1. Problem

The Company axis is wired through the dashboard, reports and list
endpoints (`v282`–`v284` / React `v224`–`v225`), but four gaps remain
that make it unusable in practice:

1. **Vehicle stat tiles ignore the filter.** With Company set to
   "Excellence (North Luzon / NORTH LUZON 2)" the list correctly reads
   "2 records (filtered)" while the tiles above read 156 / 155 / 1 / 3 /
   153. Cause: `GET /vehicles/summary` parses `branch_id` but never
   `company_id` — the same miss as `/dashboard/bootstrap`.
2. **Vehicles list still shows an "All branches" combo** beside the
   Company one.
3. **Vehicle Register Details groups by Branch** and carries no Region
   or Area column.
4. **Driver, Tire and Battery forms have no Company field at all**, so
   their list filters cannot be exercised — nothing populates Company.

## 2. Decisions taken

| Question | Decision |
|---|---|
| Vehicle form | **Branch removed; Company becomes required** |
| Driver form | **Same as Vehicle** — Branch removed, Company required |
| Tire / Battery forms | Company added (their `branch_id` is already nullable) |

## 3. The constraint that shapes everything

`vehicles.branch_id` and `drivers.branch_id` are **NOT NULL**. Removing
Branch from either form therefore requires making the column nullable.

That has a consequence which is easy to miss: `UserOrgScopeService.
covers()` short-circuits to **True** when no org context is passed, so a
record with `branch_id = NULL` would become visible to **every** user
regardless of their scope — a silent widening of access.

**This is why Company is required rather than optional.** Every new
record keeps an org anchor, and the visibility checks pass
`company_id` alongside `branch_id` so a null branch resolves through
Company instead of falling through to "visible to all".
`covers()` already accepts `company_id`, so no new scope machinery is
needed.

Tire and Battery are unaffected — already nullable, already optional.

## 4. Work, in order

### Batch 1 — Vehicle
- `GET /vehicles/summary`: accept and apply `company_id` (both the main
  count and the disposed count), so the tiles match the list.
- Vehicles list: remove the Branch combo, keep Company.
- Vehicle Register Details: group by **Company** instead of Branch;
  add **Region** and **Area/District** columns; group heading is the
  Company name. Screen and Excel export together.
- Migration: `vehicles.branch_id` → nullable.
- Vehicle form: Branch field removed; Company required.
- Visibility: pass `company_id` into `covers()` wherever vehicles are
  scoped, so a null branch does not widen access.

### Batch 2 — Driver / Assignee
- Migration: `drivers.branch_id` → nullable.
- Driver form: Branch removed, Company required.
- Driver list: Company column replacing Branch; tiles and filter by
  Company.

### Batch 3 — Tire & Battery
- Both forms gain an optional Company combo.
- Both lists: Company column replacing Branch.
- Verify the impact on Maintenance Orders and asset transactions, which
  read tire/battery org context.

### Batch 4 — Branch impact audit
Enumerate every remaining Branch surface and report it, so Michael does
not have to check screen by screen.

## 5. Testing

Every batch is TDD, red before green, and each follows the rule that has
caught each previous miss:

> **Filtering by a Company must return exactly what filtering by the
> branch mapped to that Company returns**, and a malformed `company_id`
> must be rejected rather than silently ignored.

Endpoints are **enumerated, not sampled** — both Company bugs so far
were a missed call site rather than broken logic.

Additional checks specific to this batch:
- a vehicle saved with no branch is **not** visible to a user who should
  not see it (the access-widening risk above);
- the register report's Excel export groups identically to the screen.

## 6. Out of scope here

- **MO administrative transaction types** (Change Colour, Change Engine
  with engine serial number, Change Chassis Number) writing back to the
  Vehicle master on completion. Confirmed as a separate feature; taken
  after this batch.
- Retiring the Branch master itself, moving `User.branch_id` to org
  scopes, and the `ORG_PRIMARY_AXIS` parameter — still sequenced as in
  the cutover runbook.
