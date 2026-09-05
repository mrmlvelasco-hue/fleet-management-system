"""Vehicle list endpoint for the React Master Data screens.

This is the FIRST list endpoint, and the envelope it returns
(`items` / `total` / `page` / `page_size` / `pages`) is deliberately
generic: every other Master Data list -- Drivers, Tires, Batteries,
Vendors, Branches -- will return the same shape so one frontend
scaffold can drive all of them. Changing the envelope later means
changing every consumer, so it is worth being boring here.

A note on where paging happens, because it is not obvious and it
matters:

`VehicleService.list()` applies org scoping in PYTHON, after `.all()` --
a vehicle is visible if the user created it OR their org scope covers
its branch, and the "created_by" half cannot be expressed in the same
SQL filter as the scope half. So the rows must be materialised and
filtered before they can be counted or sliced.

Paging in SQL would therefore be wrong, not merely different: LIMIT
would slice rows the user may not see, scoped-out rows would silently
consume slots on the page, and `total` would disagree with what the
list actually contains.

The cost is that the full visible set is loaded per request. For a
504-vehicle fleet that is fine. It will NOT be fine for high-volume
transaction modules (Trip Tickets, Fuel), and those will need org
scoping pushed down into SQL before they get a list endpoint. Flagged
here rather than discovered later.
"""
from flask import jsonify, request

from app.modules.api.coercion import Coercer, FieldValueError

from app.modules.api.auth import api_auth_required
from app.modules.api.routes import bp

# Sort keys the client may ask for, mapped to the attribute used. An
# allow-list rather than passing the parameter through: an unknown key
# is rejected so a column header can never claim to be sorting while
# quietly doing nothing.
_SORT_KEYS = {
    "plate": lambda v: (v.plate_number or v.conduction_number or "").upper(),
    "vehicle": lambda v: f"{v.brand} {v.model}".upper(),
    "type": lambda v: (v.vehicle_type.name if v.vehicle_type else "").upper(),
    "status": lambda v: (v.status or ""),
    "odometer": lambda v: v.current_odometer or 0,
    "year": lambda v: v.year or 0,
    "branch": lambda v: (v.branch.name if v.branch else "").upper(),
}

_VALID_STATUSES = {"ACTIVE", "INACTIVE", "IN_REPAIR", "DISPOSED"}


def _bad(message):
    return jsonify({"error": "bad_request", "message": message}), 400


def _list_filters():
    """Parse the filter/sort parameters shared by the list and its export.

    Returns (params, None) or (None, error_response).

    Extracted so `GET /vehicles` and `GET /vehicles/export.xlsx` cannot
    drift. Two parsers over the same query string is how a spreadsheet
    ends up meaning something slightly different from the screen it was
    exported from -- and the spreadsheet is the copy that gets emailed
    on, long after anyone can check it against the list.

    Paging is deliberately NOT parsed here: the export has none.
    """
    q = (request.args.get("q") or "").strip().lower()
    status = (request.args.get("status") or "").strip().upper()
    sort = (request.args.get("sort") or "plate").strip().lower()
    direction = (request.args.get("dir") or "asc").strip().lower()

    if status and status not in _VALID_STATUSES:
        # Silently ignoring a typo would show the full list under a
        # filter the user believes is applied -- every row real, nothing
        # visibly wrong.
        return None, _bad(f"Unknown status '{status}'.")
    if sort not in _SORT_KEYS:
        return None, _bad(f"Unknown sort key '{sort}'.")
    if direction not in ("asc", "desc"):
        return None, _bad("dir must be 'asc' or 'desc'.")

    branch_id = request.args.get("branch_id")
    vehicle_type_id = request.args.get("vehicle_type_id")
    try:
        branch_id = int(branch_id) if branch_id else None
        vehicle_type_id = int(vehicle_type_id) if vehicle_type_id else None
    except (TypeError, ValueError):
        return None, _bad("branch_id and vehicle_type_id must be integers.")

    return {
        "q": q or None,
        "status": status or None,
        "sort": sort,
        "direction": direction,
        "branch_id": branch_id,
        "vehicle_type_id": vehicle_type_id,
        # Disposed visibility is an EXPLICIT toggle, mirroring the Jinja
        # route's show_disposed=1 -- deliberately independent of the
        # status dropdown.
        #
        # Deriving it from `status == "DISPOSED"` instead, as this did,
        # produced an incoherence: an unfiltered search for a plate
        # returned nothing while the same search filtered to Disposed
        # returned a row. "All statuses" showing FEWER rows than one of
        # its own subsets is wrong whichever default is correct, and it
        # reads as a broken search rather than a hidden record.
        "include_disposed": request.args.get("show_disposed") == "1",
    }, None


def build_vehicle_list(api_user):
    """Filter/sort/page the caller's visible vehicles.

    Called by the existing GET /api/v1/vehicles route rather than
    registering a second one: two endpoints over the same data drift,
    and the older one already has external consumers.
    """
    filters, error = _list_filters()
    if error:
        return error

    try:
        page = int(request.args.get("page", 1))
        page_size = int(request.args.get("page_size", 20))
    except (TypeError, ValueError):
        return _bad("page and page_size must be integers.")
    if page < 1 or page_size < 1:
        return _bad("page and page_size must be 1 or greater.")
    page_size = min(page_size, 200)

    from app.modules.master_data.vehicle.service import VehicleService

    rows, total = VehicleService().list_page(
        user=api_user, page=page, page_size=page_size, **filters)

    pages = max(1, (total + page_size - 1) // page_size)
    # Enrichment runs ONLY over the page rows. PM and registration status
    # are several queries each per vehicle; measured at 14ms for 20 rows
    # against 3s for the whole fleet.
    enriched = _enrich(rows)

    return jsonify({
        "items": enriched,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": pages,
        # Kept for the original consumers of this endpoint, which predate
        # paging. Same rows, same order -- only the key names differ, so
        # nothing that reads `results`/`count` has to change.
        "results": enriched,
        "count": total,
    })


@bp.route("/vehicles/export.xlsx", methods=["GET"])
@api_auth_required("vehicle.view")
def vehicles_export(api_user):
    """The Vehicle Master list as an Excel file, respecting the filters.

    Flask's own Export button ignores every filter on the screen above
    it and dumps the whole table. This deliberately does not: exporting
    5,000 rows while the user is looking at 12 is the failure that gets
    noticed in front of a client, and the unfiltered export stays one
    click away by clearing the filters.

    Takes the SAME parameters as GET /vehicles through the SAME parser,
    and calls the SAME service method, so the file cannot contain a row
    the list would have hidden -- including org scoping, which is
    applied by list_page and never re-implemented here.

    No paging. The list caps page_size at 200; inheriting that would
    hand over a truncated file that looks complete, which is the worst
    failure available to a document someone forwards. page_size is set
    from the count so a single page holds everything.
    """
    from io import BytesIO
    from flask import send_file
    from app.core.reporting.generators import generate_vehicle_list_xlsx
    from app.modules.master_data.vehicle.service import VehicleService

    filters, error = _list_filters()
    if error:
        return error

    service = VehicleService()
    # First call establishes the size of the visible, filtered set;
    # second retrieves it whole. Cheaper than adding an unpaged code
    # path to list_page, and it cannot diverge from what the list means.
    _rows, total = service.list_page(user=api_user, page=1, page_size=1,
                                     **filters)
    rows, _total = service.list_page(user=api_user, page=1,
                                     page_size=max(total, 1), **filters)

    filename, xlsx = generate_vehicle_list_xlsx(rows)
    return send_file(
        BytesIO(xlsx), as_attachment=True, download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument"
                 ".spreadsheetml.sheet")


@bp.route("/vehicles/summary", methods=["GET"])
@api_auth_required("vehicle.view")
def vehicles_summary(api_user):
    """Counts for the stat chips.

    Split off the list request deliberately. The due counts evaluate PM
    and registration status across the whole filtered set, measured at
    ~3s for 5,000 vehicles -- twenty times the cost of the table itself.
    Bundling them would hold every keystroke of a search behind work
    nobody is waiting on, so the table paints immediately and the chips
    fill in behind it. The Jinja dashboard defers its expensive figures
    for the same reason.
    """
    # Reuses the dashboard's branch validator rather than writing a
    # second one: two implementations of "may this user filter by this
    # branch" is exactly the kind of pair that drifts apart.
    from app.modules.api.dashboard import _requested_branch_id
    branch_id, error = _requested_branch_id(api_user)
    if error:
        return error
    status = (request.args.get("status") or "").strip().upper()
    if status and status not in _VALID_STATUSES:
        return _bad(f"Unknown status '{status}'.")
    try:
        vehicle_type_id = request.args.get("vehicle_type_id")
        vehicle_type_id = int(vehicle_type_id) if vehicle_type_id else None
    except (TypeError, ValueError):
        return _bad("vehicle_type_id must be an integer.")

    from app.modules.master_data.vehicle.service import VehicleService
    rows, _ = VehicleService().list_page(
        user=api_user, q=(request.args.get("q") or "").strip() or None,
        status=status or None, vehicle_type_id=vehicle_type_id,
        branch_id=branch_id, page=1, page_size=100000,
        include_disposed=(request.args.get("show_disposed") == "1"))

    # How many disposed vehicles exist to REVEAL -- deliberately
    # independent of the status filter, so the toggle's badge does not
    # read 0 the moment any status is selected and look broken in
    # exactly the case it exists for. page_size=1 because only the
    # count is wanted; list_page filters in SQL, so this does not
    # materialise the rows.
    _disposed_rows, disposed_total = VehicleService().list_page(
        user=api_user, status="DISPOSED", branch_id=branch_id,
        page=1, page_size=1, include_disposed=True)

    summary = _summary(rows)
    summary["disposed"] = disposed_total
    return jsonify(summary)


def _serialise(v):
    return {
        "id": v.id,
        "plate_number": v.plate_number,
        "conduction_number": v.conduction_number,
        "brand": v.brand,
        "model": v.model,
        "year": v.year,
        "vehicle_type": v.vehicle_type.name if v.vehicle_type else None,
        "vehicle_type_id": v.vehicle_type_id,
        "status": v.status,
        "current_odometer": v.current_odometer,
        "branch": v.branch.name if v.branch else None,
        "branch_id": v.branch_id,
    }


def _summary(rows):
    """Counts for the stat chips above the table.

    Computed over the whole FILTERED set, not the page: "12 Active"
    means twelve in the fleet, not twelve on screen. Status counts are
    cheap (the rows are already in memory); the due counts are not, so
    they are derived from the bulk services rather than per row.
    """
    from app.core.maintenance.due_calculation_service import (
        PMDueCalculationService)
    from app.modules.registration_config.service import (
        RegistrationDueCalculationService)

    ids = {v.id for v in rows}
    pm_due = [d for d in PMDueCalculationService().get_all_due_vehicles()
              if d["vehicle"].id in ids]
    reg_due = [d for d in
               RegistrationDueCalculationService().get_all_due_vehicles()
               if d["vehicle"].id in ids]

    return {
        "total": len(rows),
        "active": sum(1 for v in rows if v.status == "ACTIVE"),
        "in_maintenance": sum(1 for v in rows if v.status == "IN_REPAIR"),
        # Distinct vehicles, not schedules: one unit with three overdue
        # services is one vehicle needing attention, and counting rows
        # would inflate the chip against the fleet size beside it.
        "pms_due_soon": len({d["vehicle"].id for d in pm_due}),
        "registration_expiring": len({d["vehicle"].id for d in reg_due}),
    }


def _enrich(window):
    """Attach assignment, next PMS and registration to the page rows."""
    from app.core.maintenance.due_calculation_service import (
        PMDueCalculationService, _Prefetch)
    from app.modules.registration_config.service import (
        RegistrationDueCalculationService)

    # _Prefetch turns ~5 queries per vehicle into zero by reading
    # reference data from memory -- the difference between 20 rows
    # costing 100 queries and costing none.
    prefetch = _Prefetch()
    pm_svc = PMDueCalculationService()
    reg_svc = RegistrationDueCalculationService()

    out = []
    for v in window:
        row = _serialise(v)
        row["assigned_driver"] = (v.assigned_driver.full_name
                                  if v.assigned_driver else None)
        row["department"] = v.department.name if v.department else None
        row["next_pms"] = _due_block(
            pm_svc.get_due_status(v, _prefetch=prefetch), km=True)
        row["registration"] = _due_block(reg_svc.get_due_status(v))
        out.append(row)
    return out


def _due_block(status: dict, km: bool = False):
    """Normalise a due-status dict into what the list column renders.

    Returns None when nothing is scheduled. A zero-day countdown would
    render as "due today" on every unscheduled vehicle -- a fleet-wide
    false alarm.
    """
    if not status:
        return None
    due_date = status.get("next_due_date")
    if due_date is None and status.get("next_due_km") is None:
        return None
    block = {
        "status": status.get("status"),
        "date": due_date.isoformat() if due_date else None,
        "days": status.get("days_remaining"),
    }
    if km:
        block["due_km"] = status.get("next_due_km")
    return block


def _money(value):
    """Decimal -> string, never float.

    A float turns 1234567.89 into binary rounding, and this figure is
    read as pesos. The fuel endpoint already follows this rule; the two
    must not disagree about how money crosses the wire.
    """
    return None if value is None else str(value)


def _iso(value):
    return value.isoformat() if value else None


_COVERS = [
    ("CTPL", "CTPL", "has_ctpl", "ctpl_from_date", "ctpl_to_date"),
    ("OD_THEFT_AON", "OD / Theft / AON", "has_od_theft_aon",
     "od_theft_aon_from_date", "od_theft_aon_to_date"),
    ("VTPL_PD", "VTPL - Property Damage", "has_vtpl_pd",
     "vtpl_pd_from_date", "vtpl_pd_to_date"),
    ("VTPL_BI", "VTPL - Bodily Injury", "has_vtpl_bi",
     "vtpl_bi_from_date", "vtpl_bi_to_date"),
]


def detail_json(v):
    """Everything vehicle_detail.html renders.

    The Jinja detail screen is the specification: it is the working,
    tested solution, and React inherits from it rather than
    approximating it. The React screen originally shipped with six of
    its eight sections missing while every test passed, which is what
    this payload -- and the parity audit in the frontend repo -- exist
    to prevent recurring.

    Derived figures come from the same services the rest of the system
    uses. Nothing here recomputes a status.
    """
    from app.core.maintenance.due_calculation_service import (
        PMDueCalculationService)
    from app.modules.registration_config.service import (
        RegistrationDueCalculationService)

    reg = RegistrationDueCalculationService().get_due_status(v) or {}
    pm = PMDueCalculationService().get_due_status(v) or {}

    return {
        # ── Basic ────────────────────────────────────────────────────
        "id": v.id,
        "conduction_number": v.conduction_number,
        "plate_number": v.plate_number,
        "vehicle_type": v.vehicle_type.name if v.vehicle_type else None,
        "vehicle_type_id": v.vehicle_type_id,
        "brand": v.brand,
        "model": v.model,
        "year": v.year,
        "variant": v.variant,
        "color": v.color,
        "fuel_type": v.fuel_type,
        "status": v.status,
        "current_odometer": v.current_odometer,
        "current_engine_hours": v.current_engine_hours,

        # ── Technical specifications ─────────────────────────────────
        "chassis_number": v.chassis_number,
        "engine_number": v.engine_number,
        "transmission": v.transmission,
        "engine_type": v.engine_type,
        "displacement": v.displacement,
        "component_group": v.component_group,
        "vehicle_body_type": v.vehicle_body_type,
        "vehicle_usage": v.vehicle_usage,

        # ── Identification & compliance ──────────────────────────────
        "mv_file_number": v.mv_file_number,
        "cr_number": v.cr_number,
        "far_number": v.far_number,
        "lto_office": v.lto_office,
        "last_known_registration_expiry": _iso(
            v.last_known_registration_expiry),
        "mr_eds": v.mr_eds,

        # ── Financial ────────────────────────────────────────────────
        "acquisition_date": _iso(v.acquisition_date),
        "acquisition_cost": _money(v.acquisition_cost),
        "assured_value_current_year": _money(v.assured_value_current_year),
        # OFFERED, not imposed. 10% compounding depreciation per year
        # from Delivery Date, computed by the model -- the same call the
        # Jinja route makes. The form prefills from this when the stored
        # value is empty and leaves it editable, so an appraised
        # override is never silently replaced. React reads this and
        # never recomputes it: a second implementation of a money figure
        # is a second answer.
        "computed_assured_value": _money(v.compute_assured_value()),
        "delivery_date": _iso(v.delivery_date),
        "top_up_amount": _money(v.top_up_amount),
        "supplier": v.supplier,
        "leasing_company": v.leasing_company,
        "with_vehicle_contract": v.with_vehicle_contract,
        "start_date": _iso(v.start_date),
        "end_date": _iso(v.end_date),

        # ── Insurance ────────────────────────────────────────────────
        # Four independent covers, each with its own dates. Collapsing
        # them into a single "insured" flag would hide which one has
        # lapsed -- and a vehicle with expired CTPL cannot legally be
        # driven even if the others are current.
        "insurance": {
            "reference_number": v.insurance_reference_number,
            "comprehensive_policy_number": v.comprehensive_policy_number,
            "comprehensive_provider": v.comprehensive_insurance_provider,
            "ctpl_policy_number": v.ctpl_policy_number,
            "ctpl_provider": v.ctpl_insurance_provider,
            "covers": [
                {
                    "code": code,
                    "label": label,
                    "active": bool(getattr(v, flag, False)),
                    "from": _iso(getattr(v, start, None)),
                    "to": _iso(getattr(v, end, None)),
                }
                for code, label, flag, start, end in _COVERS
            ],
        },

        # `has_inland_marine` has no from/to dates, unlike the other
        # four covers, so it sits outside the covers list rather than
        # being given empty dates it does not have.
        "has_inland_marine": v.has_inland_marine,

        # ── Assignment ───────────────────────────────────────────────
        # Names AND ids. The detail screen reads "Juan Dela Cruz"; the
        # form needs the id to preselect the dropdown. Returning only
        # the name left Assigned Driver rendering empty on a vehicle
        # that had one, and the next save cleared the assignment.
        "assigned_driver": (v.assigned_driver.full_name
                            if v.assigned_driver else None),
        "assigned_driver_id": v.assigned_driver_id,
        "branch": v.branch.name if v.branch else None,
        "branch_id": v.branch_id,
        "department": v.department.name if v.department else None,
        "department_id": v.department_id,
        "assignment": v.assignment,
        "assignment_group_classification": v.assignment_group_classification,
        "remarks": v.remarks,
        "notes": v.notes,

        # ── PM assignment & legacy baseline ──────────────────────────
        # The baseline is why a migrated vehicle with a high odometer
        # and no Maintenance Order on record does not read as instantly
        # overdue. Omitting it from this payload made the React form
        # unable to show or preserve it.
        "pm_schedule_id": v.pm_schedule_id,
        "last_pm_odometer": v.last_pm_odometer,
        "last_pm_date": _iso(v.last_pm_date),

        # ── Registration status ──────────────────────────────────────
        "registration_status": {
            "status": reg.get("status"),
            "plate_schedule": reg.get("plate_schedule"),
            "or_cr_number": reg.get("or_cr_number"),
            "expiry_date": _iso(reg.get("next_due_date")),
            "days_remaining": reg.get("days_remaining"),
            # The service explains WHERE the expiry came from (a
            # registration record, or the last-known fallback). Dropping
            # it leaves the reader unable to judge how much to trust it.
            "source": reg.get("source"),
        },

        # ── PM status (kept: predates the React screen) ───────────────
        "pm_status": {
            "status": pm.get("status"),
            "due_by": pm.get("due_by"),
            "due_odometer": pm.get("next_due_km"),
            "due_date": _iso(pm.get("next_due_date")),
            "reason": pm.get("reason"),
        } if pm else None,
    }


@bp.route("/vehicles/<int:vehicle_id>/maintenance-history", methods=["GET"])
@api_auth_required("vehicle.view")
def vehicle_maintenance_history(api_user, vehicle_id):
    """Completed maintenance orders for one vehicle.

    Mirrors master_data/routes.py exactly: COMPLETED only, newest
    completed_date first. Including in-flight orders here would double
    up with the Maintenance module's own open list and make the history
    read as work already done.
    """
    from app.modules.master_data.vehicle.service import VehicleService
    if VehicleService().get_visible(vehicle_id, api_user) is None:
        return jsonify({"error": "not_found",
                        "message": "Vehicle not found or not visible to "
                                   "this account."}), 404
    try:
        from app.modules.transactions.maintenance_order.models import (
            MaintenanceOrder)
    except Exception:
        # The Jinja route tolerates the transactions module being absent
        # in older phases; an empty history is the honest answer there,
        # not a 500.
        return jsonify({"items": []})

    rows = (MaintenanceOrder.query
            .filter_by(vehicle_id=vehicle_id, status="COMPLETED")
            .order_by(MaintenanceOrder.completed_date.desc())
            .all())
    return jsonify({"items": [{
        "id": m.id,
        "document_number": m.document_number,
        "completed_date": _iso(m.completed_date),
        "category": getattr(m, "category", None) or getattr(
            m, "order_category", None),
        "maintenance_type": (
            m.maintenance_type.name if getattr(m, "maintenance_type", None)
            else (m.transaction_type.name
                  if getattr(m, "transaction_type", None) else None)),
        "vendor": m.vendor.name if getattr(m, "vendor", None) else None,
        "total_cost": _money(getattr(m, "actual_cost", None)),
        "odometer": m.odometer_at_service,
        "status": m.status,
    } for m in rows]})



@bp.route("/vehicles/<int:vehicle_id>/activity-history", methods=["GET"])
@api_auth_required("vehicle.view")
def vehicle_activity_history(api_user, vehicle_id):
    """Activity timeline + outlet assignment — same sources as print."""
    from app.modules.master_data.vehicle.service import VehicleService
    vehicle = VehicleService().get_visible(vehicle_id, api_user)
    if vehicle is None:
        return jsonify({"error": "not_found",
                        "message": "Vehicle not found or not visible to "
                                   "this account."}), 404
    activity_rows, outlet_history = [], []
    try:
        from app.core.vehicle_activity_history_service import (
            VehicleActivityHistoryService)
        svc = VehicleActivityHistoryService()
        raw = svc.get_activity_rows(vehicle)
        for r in raw:
            activity_rows.append({
                "date": _iso(r.get("date")),
                "activity_type": r.get("activity_type"),
                "outlet": r.get("outlet"),
                "assigned_to": r.get("assigned_to"),
                "description": r.get("description"),
                "cost": _money(r.get("cost")),
                "odometer": r.get("odometer"),
            })
        for seg in svc.get_outlet_history(vehicle):
            outlet_history.append({
                "from_date": _iso(seg.get("from_date")),
                "to_date": _iso(seg.get("to_date")),
                "outlet": seg.get("outlet"),
                "custodian": seg.get("custodian"),
            })
    except Exception:
        activity_rows, outlet_history = [], []
    return jsonify({"activity_rows": activity_rows, "outlet_history": outlet_history})


@bp.route("/vehicles/<int:vehicle_id>/registration-history", methods=["GET"])
@api_auth_required("vehicle.view")
def vehicle_registration_history(api_user, vehicle_id):
    """Every registration document for one vehicle, newest first."""
    from app.modules.master_data.vehicle.service import VehicleService
    if VehicleService().get_visible(vehicle_id, api_user) is None:
        return jsonify({"error": "not_found",
                        "message": "Vehicle not found or not visible to "
                                   "this account."}), 404
    try:
        from app.modules.transactions.vehicle_registration.models import (
            VehicleRegistration)
    except Exception:
        return jsonify({"items": []})

    rows = (VehicleRegistration.query
            .filter_by(vehicle_id=vehicle_id)
            .order_by(VehicleRegistration.id.desc())
            .all())
    return jsonify({"items": [{
        "id": r.id,
        "document_number": r.document_number,
        "registration_type": getattr(r, "registration_type", None),
        "registration_date": _iso(getattr(r, "registration_date", None)),
        "expiry_date": _iso(getattr(r, "expiry_date", None)),
        "plate_number": getattr(r, "plate_number", None),
        "status": r.status,
    } for r in rows]})


# ── Write endpoints ─────────────────────────────────────────────────────────
#
# These REUSE VehicleService.create/update. Every validation rule lives
# there and is the reason the Flask data is clean; restating any of it
# here would produce a second definition of "a valid vehicle" that
# drifts from the one the Jinja form enforces.
#
# The only job of this layer is translating the service's exceptions into
# FIELD-level errors, so a form spanning 64 inputs can attach a message
# to the offending one instead of showing a single banner above it.

# Which input each service exception belongs against.
_FIELD_FOR_ERROR = {
    "BrandRequiredError": "brand",
    "InvalidBrandError": "brand",
    "ModelRequiredError": "model",
    "InvalidModelError": "model",
    "ModelBrandMismatchError": "model",
}

# Fields a client may set. An allow-list, not `**payload`: an unknown key
# reaching the model would either raise or silently set an attribute that
# is not a column.
_WRITABLE = [
    # Basic
    "vehicle_type_id", "brand", "model", "year", "variant", "color",
    "fuel_type", "status", "conduction_number", "plate_number",
    "current_odometer", "current_engine_hours", "vehicle_body_type",
    "vehicle_usage",
    # Identification & compliance
    "mv_file_number", "cr_number", "lto_office",
    "last_known_registration_expiry", "far_number", "mr_eds",
    # Technical
    "chassis_number", "engine_number", "transmission", "engine_type",
    "displacement", "component_group",
    # Financial
    "acquisition_date", "acquisition_cost", "assured_value_current_year",
    "delivery_date", "top_up_amount", "supplier", "leasing_company",
    "with_vehicle_contract", "start_date", "end_date",
    # Insurance
    "insurance_reference_number", "comprehensive_policy_number",
    "comprehensive_insurance_provider", "ctpl_policy_number",
    "ctpl_insurance_provider",
    "has_ctpl", "ctpl_from_date", "ctpl_to_date",
    "has_od_theft_aon", "od_theft_aon_from_date", "od_theft_aon_to_date",
    "has_vtpl_pd", "vtpl_pd_from_date", "vtpl_pd_to_date",
    "has_vtpl_bi", "vtpl_bi_from_date", "vtpl_bi_to_date",
    "has_inland_marine",
    # Assignment & remarks
    "branch_id", "department_id", "assigned_driver_id", "assignment",
    "assignment_group_classification", "remarks", "notes",
    "pm_schedule_id", "last_pm_date", "last_pm_odometer",
]

_DUPLICATE_FIELDS = (
    ("conduction number", "conduction_number"),
    ("plate number", "plate_number"),
    ("engine number", "engine_number"),
)


def _field_error(exc, payload=None, exclude_id=None):
    """Map a service exception to (field, message).

    Wording stays owned by the service; this only decides which input
    the message belongs against.

    Two shapes of duplicate error arrive here. The service's own
    pre-checks name ONE field ("Plate number 'X' already exists."). Its
    DB-constraint fallback cannot -- it raises a generic message listing
    every unique column, because at that point all it has is an
    IntegrityError. Matching phrases against the generic message would
    pick whichever field name appears first and attribute the error to
    the wrong input, which is worse than not attributing it at all.

    So when several field names appear, the submitted values are probed
    to find the one that actually collides. That is attribution of an
    error the service already raised, not a second validation rule.
    """
    if isinstance(exc, FieldValueError):
        return exc.field, str(exc)

    name = type(exc).__name__
    if name in _FIELD_FOR_ERROR:
        return _FIELD_FOR_ERROR[name], str(exc)

    text = str(exc).lower()
    matches = [field for phrase, field in _DUPLICATE_FIELDS if phrase in text]

    if len(matches) == 1:
        return matches[0], str(exc)

    if matches and payload:
        found = _colliding_unique_field(payload, exclude_id)
        if found:
            return found, str(exc)

    # Genuinely unattributable: show it at form level rather than
    # highlighting an input that may be correct.
    return "_", str(exc)


def _colliding_unique_field(payload, exclude_id=None):
    """Which submitted unique value already belongs to another vehicle."""
    from app.modules.master_data.vehicle.models import Vehicle
    for column in ("plate_number", "conduction_number", "engine_number",
                   "chassis_number"):
        value = payload.get(column)
        if not value:
            continue
        query = Vehicle.query.filter(getattr(Vehicle, column) == value)
        if exclude_id:
            query = query.filter(Vehicle.id != exclude_id)
        if query.first():
            return column
    return None


# Every writable column that is a DATE, with the label its error message
# should use. The labels match the Jinja form's, so the two apps report
# the same problem in the same words.
_DATE_FIELDS = {
    "acquisition_date": "Purchase Date",
    "delivery_date": "Delivery Date",
    "start_date": "Start Date",
    "end_date": "End Date",
    "last_pm_date": "Last PM Service Date",
    "last_known_registration_expiry": "Last Known Registration Expiry",
    "ctpl_from_date": "CTPL From Date",
    "ctpl_to_date": "CTPL To Date",
    "od_theft_aon_from_date": "OD/Theft/AON From Date",
    "od_theft_aon_to_date": "OD/Theft/AON To Date",
    "vtpl_pd_from_date": "VTPL/PD From Date",
    "vtpl_pd_to_date": "VTPL/PD To Date",
    "vtpl_bi_from_date": "VTPL/BI From Date",
    "vtpl_bi_to_date": "VTPL/BI To Date",
}


# Integer columns. The Jinja path int()s every one of these
# (routes.py 1247-1271); the API did not, so the two paths built objects
# of DIFFERENT TYPES from the same input.
_INT_FIELDS = {
    "vehicle_type_id": "Vehicle Type",
    "year": "Year",
    "current_odometer": "Current Odometer",
    "current_engine_hours": "Current Engine Hours",
    "branch_id": "Branch",
    "department_id": "Department",
    "assigned_driver_id": "Assigned Driver",
    "pm_schedule_id": "Assigned PM Template",
    "last_pm_odometer": "Last PM Service Odometer",
}

# Money. Decimal, never float: float("0.1") + float("0.2") is not 0.3,
# and an acquisition cost that drifts by a centavo is a figure the
# client will eventually reconcile against something else.
_DECIMAL_FIELDS = {
    "acquisition_cost": "Acquisition Cost",
    "assured_value_current_year": "Assured Value This Year",
    "top_up_amount": "Top-up Amount",
}


#: This module's declaration. The maps above name the fields; this
#: turns them into the shared coercer, so vehicles and drivers convert
#: values the same way rather than each growing their own copy.
_COERCER = Coercer(ints=_INT_FIELDS, decimals=_DECIMAL_FIELDS,
                   dates=_DATE_FIELDS)


def _payload_fields(payload):
    """Allow-listed fields, with JSON strings coerced to their column types.

    The Jinja path runs every date through parse_form_date before the
    service sees it (`_vehicle_fields`). The API did not, so a date
    arrived as the string "2025-03-01" and went straight to SQLAlchemy,
    which raised `SQLite Date type only accepts Python date objects`.

    That is a 500 on a well-formed request. The caller is told the
    server broke, when in fact the value was fine and simply never
    parsed -- and on the fields that reach this path most (delivery,
    start/end, last PM) it made those inputs unusable from React
    entirely.

    Reuses parse_form_date rather than parsing here: it already owns the
    wording, and a second parser would be a second opinion on what
    counts as a valid date.

    NUMERICS need the same treatment, for a failure that is easy to miss
    because the data lands correctly. JSON has no separate integer
    input and the React form holds every value as a string, so
    `current_odometer` arrives as "42000". SQLAlchemy coerces on FLUSH,
    so the row is written properly and a later GET reads back an int --
    but the in-memory object still holds the string, and detail_json
    runs the PM due calculation against that same object immediately
    after the write:

        TypeError: '>=' not supported between instances of 'str' and 'int'

    So the update SUCCEEDS and then 500s while reporting its own result.
    The user sees a failed save for a change that was in fact committed,
    which is worse than a clean failure: retrying appears to do nothing.

    Coerced here rather than defended against in the due-calculation
    service. The comparison there is correct; being handed a string is
    the defect, and one input path producing differently-typed objects
    from another is the thing to fix.
    """
    return _COERCER.fields(payload, _WRITABLE)


def _write_errors():
    from app.core.validation.date_utils import (
        DateFormatError, RequiredFieldError)
    from app.modules.master_data.vehicle import service as vsvc
    return (FieldValueError, DateFormatError,
            RequiredFieldError) + tuple(
        getattr(vsvc, n) for n in (
            "DuplicateVehicleError", "InvalidVehicleDataError",
            "BrandRequiredError", "ModelRequiredError", "InvalidBrandError",
            "InvalidModelError", "ModelBrandMismatchError")
        if hasattr(vsvc, n))


@bp.route("/vehicles", methods=["POST"])
@api_auth_required("vehicle.create")
def create_vehicle(api_user):
    from app.extensions import db
    from app.modules.master_data.vehicle.service import VehicleService

    payload = request.get_json(silent=True) or {}
    try:
        # Inside the try: date coercion raises DateFormatError, and a
        # bad date is the caller's mistake. Leaving this outside would
        # let it escape as a 500 -- telling the caller the server broke
        # when in fact their value was simply not a date.
        fields = _payload_fields(payload)
    except _write_errors() as exc:
        field, message = _field_error(exc, payload)
        return jsonify({"error": "validation", "message": message,
                        "fields": {field: message}}), 400
    # Vehicle.year is NOT NULL, and the service takes it positionally.
    # Defaulting it to None produced an IntegrityError, which the
    # service reports through its generic duplicate message -- so a
    # missing year surfaced as "a unique field is already used", which
    # is both wrong and unactionable. Rejected explicitly instead.
    if not fields.get("year"):
        return jsonify({
            "error": "validation",
            "message": "Year is required.",
            "fields": {"year": "Year is required."}}), 400
    try:
        # strict=True mirrors the Jinja route exactly: brand and model
        # must resolve against the master list. Passing False here would
        # let the API create rows the web form would have rejected, and
        # free-text brands are precisely what the master list exists to
        # prevent.
        vehicle = VehicleService().create(strict=True, **fields)
    except _write_errors() as exc:
        # The service raises before committing, but rolling back keeps
        # the session clean for anything the request does afterwards.
        db.session.rollback()
        field, message = _field_error(exc, fields)
        return jsonify({"error": "validation",
                        "message": message,
                        "fields": {field: message}}), 400
    return jsonify(detail_json(vehicle)), 201


@bp.route("/vehicles/<int:vehicle_id>", methods=["PUT", "PATCH"])
@api_auth_required("vehicle.update")
def update_vehicle(api_user, vehicle_id):
    from app.extensions import db
    from app.modules.master_data.vehicle.service import VehicleService

    svc = VehicleService()
    # Visibility first, and the same 404 the detail endpoint gives, so a
    # write attempt cannot be used to discover which ids exist.
    if svc.get_visible(vehicle_id, api_user) is None:
        return jsonify({"error": "not_found",
                        "message": "Vehicle not found or not visible to "
                                   "this account."}), 404

    payload = request.get_json(silent=True) or {}
    try:
        fields = _payload_fields(payload)
        vehicle = svc.update(vehicle_id, strict=True, **fields)
    except _write_errors() as exc:
        db.session.rollback()
        field, message = _field_error(exc, fields, exclude_id=vehicle_id)
        return jsonify({"error": "validation",
                        "message": message,
                        "fields": {field: message}}), 400
    return jsonify(detail_json(vehicle))


# ── Register report & print ─────────────────────────────────────────────────

@bp.route("/reports/vehicle-register", methods=["GET"])
@api_auth_required("reportvehicleregister.view")
def vehicle_register_report(api_user):
    """Vehicle Register Details, grouped by branch.

    Its own permission, as in Flask: a reporting export over the whole
    fleet is a different disclosure from browsing one record at a time,
    and vehicle.view must not imply it.

    Rows come from VehicleRegisterReportService -- the same service the
    Jinja report renders -- so this cannot show a branch the browser
    list would hide, nor disagree with the printed copy.
    """
    from datetime import datetime
    from app.modules.master_data.vehicle.report_service import (
        VehicleRegisterReportService)

    groups = VehicleRegisterReportService().get_grouped(user=api_user)
    branch_id = request.args.get("branch_id")
    if branch_id:
        try:
            wanted = int(branch_id)
        except (TypeError, ValueError):
            return _bad("branch_id must be an integer.")
        groups = [g for g in groups
                  if any(r.get("branch_id") == wanted
                         for r in g.get("vehicles", []))]

    return jsonify({
        "groups": groups,
        # A printed register with no timestamp cannot be told apart from
        # one printed last quarter.
        "generated_at": datetime.now().isoformat(),
    })


@bp.route("/reports/vehicle-register/export.xlsx", methods=["GET"])
@api_auth_required("reportvehicleregister.view")
def vehicle_register_export(api_user):
    """The same spreadsheet the Jinja page downloads.

    Reuses generate_vehicle_register_xlsx rather than building a second
    workbook: two files with the same name and different columns would
    leave whoever received one unable to tell which they had.
    """
    from io import BytesIO
    from flask import send_file
    from app.core.reporting.generators import generate_vehicle_register_xlsx

    filters = {}
    branch_id = request.args.get("branch_id")
    if branch_id:
        try:
            filters["branch_id"] = int(branch_id)
        except (TypeError, ValueError):
            return _bad("branch_id must be an integer.")

    filename, xlsx = generate_vehicle_register_xlsx(filters=filters,
                                                    user=api_user)
    return send_file(
        BytesIO(xlsx), as_attachment=True, download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument"
                 ".spreadsheetml.sheet")


@bp.route("/vehicles/<int:vehicle_id>/clone", methods=["GET"])
@api_auth_required("vehicle.create")
def clone_vehicle(api_user, vehicle_id):
    """Field values for prefilling a NEW vehicle from an existing one.

    Wraps VehicleService.get_clone_data, which already owns the rule
    about what a clone may carry: plate, conduction, chassis and engine
    numbers are excluded there, because a clone inheriting them would
    collide with the original -- and a duplicate chassis number on a
    fleet register is the kind of data problem nobody notices until an
    audit.

    Gated on `vehicle.create`, not `vehicle.view`: this is a draft of a
    new record, and a read-only user has no use for a prefilled form
    they cannot submit.

    Creates NOTHING. The clone exists only in the browser until the
    user fills in the identifiers and saves, which mirrors Flask --
    there, this route renders the form rather than writing a row.
    """
    from datetime import date
    from decimal import Decimal

    from app.modules.master_data.vehicle.service import VehicleService

    svc = VehicleService()
    # Visibility first, and the same 404 the detail endpoint gives. A
    # clone returns nearly every column, so without this it would be a
    # way to read a vehicle the caller cannot otherwise see.
    if svc.get_visible(vehicle_id, api_user) is None:
        return jsonify({"error": "not_found",
                        "message": "Vehicle not found or not visible to "
                                   "this account."}), 404

    data = svc.get_clone_data(vehicle_id)
    if not data:
        return jsonify({"error": "not_found",
                        "message": "Vehicle not found."}), 404

    # get_clone_data returns RAW column values -- date and Decimal
    # objects, which jsonify cannot encode. Converted with the same
    # helpers detail_json uses, so a cloned date reaches the form in the
    # format its input expects rather than as a Python repr.
    out = {}
    for key, value in data.items():
        if isinstance(value, date):
            out[key] = _iso(value)
        elif isinstance(value, Decimal):
            out[key] = _money(value)
        else:
            out[key] = value
    return jsonify(out)


@bp.route("/vehicles/<int:vehicle_id>/print", methods=["GET"])
@api_auth_required("vehicle.view")
def vehicle_print(api_user, vehicle_id):
    """Everything the printable vehicle sheet needs.

    Returns the SAME detail payload the screen uses plus the company
    letterhead and a timestamp, and lets the client lay it out. A
    server-rendered HTML page would be a second definition of what a
    vehicle record contains, and the two would drift.
    """
    from datetime import datetime
    from app.modules.master_data.vehicle.service import VehicleService

    vehicle = VehicleService().get_visible(vehicle_id, api_user)
    if vehicle is None:
        return jsonify({"error": "not_found",
                        "message": "Vehicle not found or not visible to "
                                   "this account."}), 404

    from app.modules.system_admin.services.company_service import (
        CompanyProfileService)
    company = CompanyProfileService().get()

    def _iso(v):
        if v is None:
            return None
        if hasattr(v, "isoformat"):
            return v.isoformat()
        return str(v)

    def _num(v):
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return str(v)

    attachments = []
    doc_type_labels = {}
    try:
        from app.core.attachments.attachment_service import AttachmentService
        svc = AttachmentService()
        attachments = [{
            "id": a.id,
            "original_filename": a.original_filename,
            "mime_type": a.mime_type,
            "document_type": getattr(a, "document_type", None),
            "file_size": a.file_size,
        } for a in svc.list_for("vehicles", vehicle_id)]
        doc_type_labels = {row.code: row.description
                           for row in svc.document_types()}
    except Exception:
        attachments = []

    mo_history = []
    try:
        from app.modules.transactions.maintenance_order.models import (
            MaintenanceOrder)
        orders = (MaintenanceOrder.query
                  .filter_by(vehicle_id=vehicle_id, status="COMPLETED")
                  .order_by(MaintenanceOrder.scheduled_date.desc())
                  .all())
        for mo in orders:
            mo_history.append({
                "id": mo.id,
                "document_number": mo.document_number,
                "type": (
                    mo.maintenance_type.name if mo.maintenance_type
                    else (mo.transaction_type.name if mo.transaction_type
                          else None)),
                "category": mo.category or mo.order_category,
                "scheduled_date": _iso(mo.scheduled_date),
                "completed_date": _iso(mo.completed_date),
                "odometer": mo.odometer_at_service,
                "status": mo.status,
                "cost": _num(mo.actual_cost or mo.estimated_cost),
            })
    except Exception:
        mo_history = []

    activity_rows = []
    utilization = {}
    outlet_history = []
    try:
        from app.core.vehicle_activity_history_service import (
            VehicleActivityHistoryService)
        activity_svc = VehicleActivityHistoryService()
        raw_rows = activity_svc.get_activity_rows(vehicle)
        utilization = dict(activity_svc.get_utilization_summary(vehicle, raw_rows) or {})
        if utilization.get("total_maintenance_cost") is not None:
            utilization["total_maintenance_cost"] = _num(
                utilization["total_maintenance_cost"])
        outlet_history = []
        for seg in activity_svc.get_outlet_history(vehicle):
            outlet_history.append({
                "from_date": _iso(seg.get("from_date")),
                "to_date": _iso(seg.get("to_date")),
                "outlet": seg.get("outlet"),
                "custodian": seg.get("custodian"),
            })
        for r in raw_rows:
            activity_rows.append({
                "date": _iso(r.get("date")),
                "activity_type": r.get("activity_type"),
                "outlet": r.get("outlet"),
                "assigned_to": r.get("assigned_to"),
                "description": r.get("description"),
                "cost": _num(r.get("cost")),
                "odometer": r.get("odometer"),
            })
    except Exception:
        activity_rows = []
        utilization = {}
        outlet_history = []

    addr1 = None
    if company:
        addr1 = (getattr(company, "address_line1", None)
                 or getattr(company, "address_line", None))

    return jsonify({
        "vehicle": detail_json(vehicle),
        "company": {
            "company_name": getattr(company, "company_name", None),
            "address_line": addr1,
            "address_line1": addr1,
            "city": getattr(company, "city", None),
        } if company else {},
        "generated_at": datetime.now().isoformat(),
        "attachments": attachments,
        "attachment_doc_types": doc_type_labels,
        "mo_history": mo_history,
        "activity_rows": activity_rows,
        "utilization": utilization,
        "outlet_history": outlet_history,
    })


@bp.route("/vehicle-types", methods=["GET"])
@api_auth_required("vehicle.view")
def vehicle_types(api_user):
    """Vehicle types for the form's required dropdown.

    Gated on vehicle.view rather than vehicletype.view: this is
    reference data needed to READ or fill a vehicle form, and requiring
    the master-data permission would leave the dropdown empty for
    exactly the people expected to enrol vehicles -- a required field
    with no options, which is a form that cannot be submitted.
    """
    from app.modules.master_data.reference.service import VehicleTypeService
    rows = VehicleTypeService().list()
    return jsonify({"items": [
        {"id": t.id, "code": t.code, "name": t.name,
         "category": getattr(t, "category", None)}
        for t in rows]})


@bp.route("/vehicles/odometer-logs", methods=["GET"])
@api_auth_required("vehicle.view")
def vehicle_odometer_logs(api_user):
    """The odometer audit list.

    The client's request: a way to check what was pushed from the phone.
    Filterable by vehicle, source and date, because "which readings came
    from mobile last week" is the question actually being asked.
    """
    from app.modules.api.pagination import resolve_page_size
    from app.modules.master_data.vehicle.odometer_service import (
        OdometerService)

    rows, total = OdometerService().history(
        vehicle_id=request.args.get("vehicle_id", type=int),
        source=request.args.get("source") or None,
        date_from=request.args.get("date_from") or None,
        date_to=request.args.get("date_to") or None,
        page=request.args.get("page", 1, type=int),
        per_page=resolve_page_size(request.args.get("per_page")))
    return jsonify({
        "items": [{
            "id": e.id,
            "vehicle_id": e.vehicle_id,
            "plate_number": (e.vehicle.plate_number
                             or e.vehicle.conduction_number) if e.vehicle else None,
            "reading": e.reading,
            "previous_reading": e.previous_reading,
            "delta": e.delta,
            "source": e.source,
            "recorded_at": e.recorded_at.isoformat() if e.recorded_at else None,
            "recorded_by": e.user.full_name if e.user else None,
            "remarks": e.remarks,
        } for e in rows],
        "total": total,
    })
