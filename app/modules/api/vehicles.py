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


def build_vehicle_list(api_user):
    """Filter/sort/page the caller's visible vehicles.

    Called by the existing GET /api/v1/vehicles route rather than
    registering a second one: two endpoints over the same data drift,
    and the older one already has external consumers.
    """
    q = (request.args.get("q") or "").strip().lower()
    status = (request.args.get("status") or "").strip().upper()
    sort = (request.args.get("sort") or "plate").strip().lower()
    direction = (request.args.get("dir") or "asc").strip().lower()

    if status and status not in _VALID_STATUSES:
        # Silently ignoring a typo would show the full list under a
        # filter the user believes is applied -- every row real, nothing
        # visibly wrong.
        return _bad(f"Unknown status '{status}'.")
    if sort not in _SORT_KEYS:
        return _bad(f"Unknown sort key '{sort}'.")
    if direction not in ("asc", "desc"):
        return _bad("dir must be 'asc' or 'desc'.")

    try:
        page = int(request.args.get("page", 1))
        page_size = int(request.args.get("page_size", 20))
    except (TypeError, ValueError):
        return _bad("page and page_size must be integers.")
    if page < 1 or page_size < 1:
        return _bad("page and page_size must be 1 or greater.")
    page_size = min(page_size, 200)

    branch_id = request.args.get("branch_id")
    vehicle_type_id = request.args.get("vehicle_type_id")
    try:
        branch_id = int(branch_id) if branch_id else None
        vehicle_type_id = int(vehicle_type_id) if vehicle_type_id else None
    except (TypeError, ValueError):
        return _bad("branch_id and vehicle_type_id must be integers.")

    from app.modules.master_data.vehicle.service import VehicleService

    # DISPOSED units are excluded from the everyday list by the service,
    # but must be reachable when explicitly filtered for -- otherwise
    # asking for disposed vehicles returns nothing, which reads as
    # "there are none" rather than "the list will not show you those".
    rows, total = VehicleService().list_page(
        user=api_user, q=q or None, status=status or None,
        vehicle_type_id=vehicle_type_id, branch_id=branch_id,
        sort=sort, direction=direction, page=page, page_size=page_size,
        include_disposed=(status == "DISPOSED"))

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
        include_disposed=(status == "DISPOSED"))
    return jsonify(_summary(rows))


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

        # ── Identification & compliance ──────────────────────────────
        "mv_file_number": v.mv_file_number,
        "lto_office": v.lto_office,
        "last_known_registration_expiry": _iso(
            v.last_known_registration_expiry),

        # ── Financial ────────────────────────────────────────────────
        "acquisition_date": _iso(v.acquisition_date),
        "acquisition_cost": _money(v.acquisition_cost),
        "assured_value_current_year": _money(v.assured_value_current_year),
        "delivery_date": _iso(v.delivery_date),

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

        # ── Assignment ───────────────────────────────────────────────
        "assigned_driver": (v.assigned_driver.full_name
                            if v.assigned_driver else None),
        "branch": v.branch.name if v.branch else None,
        "branch_id": v.branch_id,
        "department": v.department.name if v.department else None,
        "remarks": v.remarks,

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
        "total_cost": _money(getattr(m, "total_cost", None)),
    } for m in rows]})


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
