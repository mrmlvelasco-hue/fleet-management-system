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
