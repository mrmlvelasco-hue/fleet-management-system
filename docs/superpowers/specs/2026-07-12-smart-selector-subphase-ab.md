# Smart Selector — Sub-phase A + B Implementation Plan

**Sub-phase A — Core infrastructure**
- `SearchableService` base class (core/search/) — generic paginated,
  multi-field search against any model, Select2-compatible response shape.
- `/api/search/<module>` blueprint — one endpoint per registered module,
  permission-gated on that module's existing `.view` permission.
- JS helper `initAjaxSelect(selector, endpoint, opts)` in app.js — wires a
  `<select>` to Select2's AJAX remote-data mode (debounced, paginated).

**Sub-phase B — Rollout to Vehicles, Drivers, Users, Vendors**
- Register each as a searchable module (search fields + label formatter).
- Convert existing full-list-preload `<select>` elements in the highest-
  traffic forms to AJAX mode:
  - Vehicle: Trip Ticket, ATD, Vehicle Movement, Maintenance Order,
    Tire Txn, Battery Txn (6 forms)
  - Driver: Trip Ticket, ATD (2 forms)
  - Vendor: Tire master, Battery master, Maintenance Order, Purchase
    Request (4 forms)
  - User: Notification Rule (specific_user) (1 form)
- Edit forms keep working: when editing a record with an existing
  selection, the route still passes that one record so Select2 can
  pre-render it as the initial option; new/blank searches go through AJAX.

Search field definitions:
- Vehicle: plate_number, conduction_number, brand, model (plate/conduction
  weighted first per requirement — matched first, checked before brand/model)
- Driver: employee_number, first_name, last_name
- User: username, first_name, last_name
- Vendor: code, name

Response contract: `{"results": [{"id", "text"}], "pagination": {"more": bool}}`
(Select2's expected AJAX shape).

Permission: each search endpoint requires the same `<module>.view`
permission already enforced on that module's list screen — no new
permissions needed.
