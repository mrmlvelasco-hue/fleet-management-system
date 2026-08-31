"""Canned report endpoints (JSON for the screen, XLSX for export).

Each report exposes two routes that share one filter parse and one row
source, so the on-screen table and the downloaded spreadsheet cannot
disagree -- the same equivalence the generic approval endpoints hold to.
The XLSX side reuses the existing generators in
app.core.reporting.generators rather than building a second workbook: two
files with the same name and different columns would leave whoever
received one unable to tell which they had.

Parity source for each is the matching route in
system_admin/routes.py; see docs/parity-report-*.md.
"""
from datetime import datetime
from io import BytesIO

from flask import jsonify, request, send_file

from app.modules.api.auth import api_auth_required
from app.modules.api.routes import bp

_XLSX_MIME = ("application/vnd.openxmlformats-officedocument"
              ".spreadsheetml.sheet")


def _bad(message):
    return jsonify({"error": "bad_request", "message": message}), 400


def _int_arg(name):
    """Parse an int query arg. Returns (value, error_response). A blank
    or absent arg is (None, None); a non-integer is a clean 400 rather
    than a 500 from int() deep in a service."""
    raw = request.args.get(name)
    if raw is None or raw == "":
        return None, None
    try:
        return int(raw), None
    except (TypeError, ValueError):
        return None, _bad(f"{name} must be an integer.")


def _send_xlsx(filename, data):
    return send_file(BytesIO(data), as_attachment=True,
                     download_name=filename, mimetype=_XLSX_MIME)


# ── PMS Compliance / Due ─────────────────────────────────────────────────────

def _pms_filters():
    """The four filter keys, parsed and validated. Returns (filters,
    error_response)."""
    filters = {}
    for key in ("branch_id", "vehicle_type_id", "maintenance_type_id"):
        val, err = _int_arg(key)
        if err:
            return None, err
        if val is not None:
            filters[key] = val
    status = request.args.get("status")
    if status:
        filters["status"] = status
    return filters, None


def _pms_rows(filters, user):
    """The same rows report_pms_compliance builds, in the same order,
    with the same org-scope and maintenance-type rules."""
    from app.core.maintenance.due_calculation_service import (
        PMDueCalculationService)
    from app.modules.user_management.org_scope_service import (
        UserOrgScopeService)
    from app.core.reporting.generators import _vehicle_matches

    scope = UserOrgScopeService()
    rows = [r for r in PMDueCalculationService().get_all_due_vehicles()
            if scope.covers(user.id, branch_id=r["vehicle"].branch_id)
            and _vehicle_matches(r["vehicle"], filters)]
    if filters.get("status"):
        rows = [r for r in rows if r["status"] == filters["status"]]
    if filters.get("maintenance_type_id"):
        # A vehicle with no applicable schedule has no maintenance type,
        # so it cannot match a specific type filter -- excluded, never
        # shown under whichever type happened to be selected. Same rule
        # as the Jinja report and the generator.
        want = filters["maintenance_type_id"]
        rows = [r for r in rows
                if r.get("schedule") is not None
                and r["schedule"].maintenance_type_id == want]
    return rows


def _pms_row_json(r):
    v = r["vehicle"]
    sched = r.get("schedule")
    return {
        "plate_no": v.plate_number or v.conduction_number,
        "branch": v.branch.name if v.branch else None,
        "cost_center": v.cost_center or None,
        "make": v.brand,
        "model": v.model,
        "maintenance_type": (sched.maintenance_type.name
                             if sched and sched.maintenance_type else None),
        "next_due_km": r.get("next_due_km"),
        "current_odometer": v.current_odometer,
        "next_due_date": (r["next_due_date"].isoformat()
                          if r.get("next_due_date")
                          and hasattr(r["next_due_date"], "isoformat")
                          else r.get("next_due_date")),
        "status": r["status"],
    }


@bp.route("/reports/pms-compliance", methods=["GET"])
@api_auth_required("reportpmscompliance.view")
def report_pms_compliance(api_user):
    filters, err = _pms_filters()
    if err:
        return err

    # Do NOT run on a bare open. get_all_due_vehicles evaluates every
    # active vehicle against every schedule -- the heaviest query here.
    # rows stays null until an explicit generate=1 or a filter, which is
    # also how the screen tells "not run yet" from "ran, nothing
    # matched".
    should_run = request.args.get("generate") == "1" or bool(filters)
    if not should_run:
        return jsonify({"rows": None})

    rows = _pms_rows(filters, api_user)
    return jsonify({
        "rows": [_pms_row_json(r) for r in rows],
        "generated_at": datetime.now().isoformat(),
    })


@bp.route("/reports/pms-compliance/export.xlsx", methods=["GET"])
@api_auth_required("reportpmscompliance.view")
def report_pms_compliance_export(api_user):
    filters, err = _pms_filters()
    if err:
        return err
    from app.core.reporting.generators import generate_pms_compliance_xlsx
    filename, data = generate_pms_compliance_xlsx(filters, user=api_user)
    return _send_xlsx(filename, data)


# ── Vehicle Registration Expiry ──────────────────────────────────────────────

def _registration_filters():
    filters = {}
    for key in ("branch_id", "vehicle_type_id"):
        val, err = _int_arg(key)
        if err:
            return None, err
        if val is not None:
            filters[key] = val
    status = request.args.get("status")
    if status:
        filters["status"] = status
    return filters, None


def _registration_rows(filters, user):
    """The rows report_registration_expiry builds. Every status is
    fetched (not just DUE_SOON/OVERDUE) so the status filter's other
    options have data, then narrowed by the chosen status -- exactly as
    the Jinja route does. Org scope applies regardless."""
    from app.modules.registration_config.service import (
        RegistrationDueCalculationService)
    from app.modules.user_management.org_scope_service import (
        UserOrgScopeService)
    from app.core.reporting.generators import _vehicle_matches

    scope = UserOrgScopeService()
    all_statuses = ("OVERDUE", "DUE_SOON", "GOOD", "NO_RECORD")
    rows = [r for r in RegistrationDueCalculationService().get_all_due_vehicles(
                statuses=all_statuses)
            if scope.covers(user.id, branch_id=r["vehicle"].branch_id)
            and _vehicle_matches(r["vehicle"], filters)]
    if filters.get("status"):
        rows = [r for r in rows if r["status"] == filters["status"]]
    return rows


def _registration_row_json(r):
    v = r["vehicle"]
    due = r.get("next_due_date")
    return {
        "plate_no": v.plate_number or v.conduction_number,
        "branch": v.branch.name if v.branch else None,
        "make": v.brand,
        "model": v.model,
        "lto_month": r.get("lto_month"),
        "lto_week": r.get("lto_week"),
        "next_due_date": (due.isoformat()
                          if due and hasattr(due, "isoformat") else due),
        "source": r.get("source"),
        "status": r["status"],
        "warning": r.get("warning"),
    }


@bp.route("/reports/registration-expiry", methods=["GET"])
@api_auth_required("reportregistrationexpiry.view")
def report_registration_expiry(api_user):
    filters, err = _registration_filters()
    if err:
        return err
    # No run gate: the Jinja route runs this on open. get_all_due_vehicles
    # here is a status lookup per vehicle, not the full schedule scan the
    # PMS report does, so it is cheap enough to run unconditionally.
    rows = _registration_rows(filters, api_user)
    return jsonify({
        "rows": [_registration_row_json(r) for r in rows],
        "generated_at": datetime.now().isoformat(),
    })


@bp.route("/reports/registration-expiry/export.xlsx", methods=["GET"])
@api_auth_required("reportregistrationexpiry.view")
def report_registration_expiry_export(api_user):
    filters, err = _registration_filters()
    if err:
        return err
    from app.core.reporting.generators import (
        generate_registration_expiry_xlsx)
    filename, data = generate_registration_expiry_xlsx(filters, user=api_user)
    return _send_xlsx(filename, data)


# ── Maintenance Cost Summary ─────────────────────────────────────────────────

def _cost_filters():
    filters = {}
    for key in ("branch_id", "vehicle_type_id"):
        val, err = _int_arg(key)
        if err:
            return None, err
        if val is not None:
            filters[key] = val
    for key in ("date_from", "date_to", "plate_number"):
        val = request.args.get(key)
        if val:
            filters[key] = val.strip() if key == "plate_number" else val
    return filters, None


def _cost_orders(filters, user):
    """The same COMPLETED orders report_maintenance_cost_summary builds,
    in the same order (completed_date descending), with the same
    plate/conduction fallback and org-scope rules."""
    from app.modules.transactions.maintenance_order.models import (
        MaintenanceOrder)
    from app.modules.user_management.org_scope_service import (
        UserOrgScopeService)

    query = MaintenanceOrder.query.filter_by(status="COMPLETED")
    if filters.get("branch_id"):
        query = query.filter(MaintenanceOrder.vehicle.has(
            branch_id=filters["branch_id"]))
    if filters.get("vehicle_type_id"):
        query = query.filter(MaintenanceOrder.vehicle.has(
            vehicle_type_id=filters["vehicle_type_id"]))
    if filters.get("date_from"):
        query = query.filter(
            MaintenanceOrder.completed_date >= filters["date_from"])
    if filters.get("date_to"):
        query = query.filter(
            MaintenanceOrder.completed_date <= filters["date_to"])
    orders = query.order_by(MaintenanceOrder.completed_date.desc()).all()

    if filters.get("plate_number"):
        # Case-insensitive substring against EITHER column -- checked in
        # Python, not SQL, matching the Jinja route: unregistered
        # vehicles have no plate yet, so the fallback to
        # conduction_number is what makes them findable at all.
        needle = filters["plate_number"].lower()
        orders = [o for o in orders
                  if needle in (o.vehicle.plate_number or "").lower()
                  or needle in (o.vehicle.conduction_number or "").lower()]

    scope = UserOrgScopeService()
    orders = [o for o in orders
              if scope.covers(user.id, branch_id=o.vehicle.branch_id)]
    return orders


def _cost_row_json(o):
    v = o.vehicle
    return {
        "mo_number": o.document_number or "(draft)",
        "vehicle": f"{v.plate_number or v.conduction_number} — "
                   f"{v.brand} {v.model}",
        "branch": v.branch.name if v.branch else None,
        "category": o.category or o.order_category,
        "maintenance_type": (o.maintenance_type.name
                             if o.maintenance_type else None),
        "completed_date": (o.completed_date.isoformat()
                           if o.completed_date else None),
        "actual_cost": float(o.actual_cost or 0),
    }


def _budget_rows_json(orders):
    """Budget Utilization by Vehicle: one row per DISTINCT vehicle in
    the already-filtered `orders`, first occurrence wins, included only
    when applicable. This is the section the XLSX export does not
    carry -- Flask's own generator omits it, so this endpoint's export
    route must not add it either; that would be scope beyond parity,
    not a bug fix.

    `spent` inside get_budget_status is deliberately NOT derived from
    `orders` -- it queries the vehicle's own budget-year window,
    anchored to its delivery/acquisition date, which is independent of
    whatever date range the report itself was filtered to.
    """
    from app.core.maintenance.budget_service import VehicleBudgetService

    budget_svc = VehicleBudgetService()
    seen = set()
    rows = []
    for o in orders:
        if o.vehicle_id in seen:
            continue
        seen.add(o.vehicle_id)
        status = budget_svc.get_budget_status(o.vehicle)
        if not status["applicable"]:
            continue
        rows.append({
            "vehicle_id": o.vehicle_id,
            "vehicle": f"{o.vehicle.plate_number or o.vehicle.conduction_number}",
            "mode": status["mode"],
            "classification": status["classification"],
            "current_year": status["current_year"],
            "period_start": status["period_start"].isoformat(),
            "period_end": status["period_end"].isoformat(),
            "budget": float(status["budget"]),
            "spent": float(status["spent"]),
            "remaining": float(status["remaining"]),
            "over_budget": status["over_budget"],
        })
    return rows


@bp.route("/reports/maintenance-cost", methods=["GET"])
@api_auth_required("reportmaintenancecost.view")
def report_maintenance_cost(api_user):
    filters, err = _cost_filters()
    if err:
        return err
    # No run gate: a COMPLETED-status query is cheap regardless of fleet
    # size, unlike PMS's full schedule scan.
    orders = _cost_orders(filters, api_user)
    total = sum(float(o.actual_cost or 0) for o in orders)
    return jsonify({
        "rows": [_cost_row_json(o) for o in orders],
        # Computed server-side over the SAME filtered set the rows come
        # from. The screen must never re-sum the rows itself -- a
        # paginated view re-summing only its visible page would
        # silently disagree with this figure.
        "total": total,
        "budget_rows": _budget_rows_json(orders),
        "generated_at": datetime.now().isoformat(),
    })


@bp.route("/reports/maintenance-cost/export.xlsx", methods=["GET"])
@api_auth_required("reportmaintenancecost.view")
def report_maintenance_cost_export(api_user):
    filters, err = _cost_filters()
    if err:
        return err
    from app.core.reporting.generators import (
        generate_maintenance_cost_summary_xlsx)
    filename, data = generate_maintenance_cost_summary_xlsx(
        filters, user=api_user)
    return _send_xlsx(filename, data)
