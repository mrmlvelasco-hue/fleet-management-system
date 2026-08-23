"""Dashboard endpoints for the React frontend.

This module is deliberately THIN. It computes nothing.

Every figure it returns comes from DashboardService or
DashboardAnalyticsService -- the exact services the Jinja dashboard
already calls. That is the single most important property of this file
and the reason to resist "just query it here, it's only a COUNT":

A second implementation would drift. Not immediately, but the first
time someone changes what "fleet" excludes, or adjusts the due-window,
or fixes a scoping bug, they will change it in one place. Then the two
dashboards disagree, and the person who notices has no way to tell
which number is the real one -- which is strictly worse than either
being wrong on its own, because it destroys trust in both. This project
has already been bitten by exactly that shape of bug: the Registrations
card once read 0 while the list beneath it showed 1, because the count
and the list used different lookups.

So: if a number is needed here and no service provides it, the fix is
to add it to the service, not to compute it here.
"""
from datetime import date as _date

from flask import jsonify, request

from app.core.dashboard_service import DashboardService
from app.core.dashboard_analytics_service import DashboardAnalyticsService
from app.modules.api.auth import api_auth_required
from app.modules.api.routes import bp


def _requested_branch_id(user):
    """Parse and validate ?branch_id=.

    Returns (branch_id_or_None, error_response_or_None).

    An unknown or unreadable branch id is REJECTED rather than ignored.
    Silently dropping it would render a full company-wide dashboard to
    someone who believes they are looking at one branch -- the numbers
    would be real, which is what makes it dangerous: nothing on screen
    would look wrong.
    """
    raw = request.args.get("branch_id")
    if raw is None or raw == "":
        return None, None
    try:
        branch_id = int(raw)
    except (TypeError, ValueError):
        return None, (jsonify({
            "error": "bad_request",
            "message": "branch_id must be an integer."}), 400)

    from app.modules.master_data.org.models import Branch
    from app.modules.user_management.org_scope_service import (
        UserOrgScopeService)

    branch = Branch.query.filter_by(id=branch_id, is_active=True).first()
    if branch is None:
        return None, (jsonify({
            "error": "bad_request",
            "message": f"No active branch with id {branch_id}."}), 400)

    # Same 404-shaped answer for "doesn't exist" and "you can't see it",
    # so the endpoint can't be used to enumerate branch ids the caller
    # has no access to.
    if not UserOrgScopeService().covers(user.id, branch_id=branch_id):
        return None, (jsonify({
            "error": "bad_request",
            "message": f"No active branch with id {branch_id}."}), 400)
    return branch_id, None


def _availability(fleet_status: dict):
    """Share of the fleet that is ACTIVE, from the real status counts.

    Returns None -- not 0.0 -- when there are no vehicles at all. Zero
    would read as "nothing is available", an alarming and false claim,
    where the truth is simply that the ratio is undefined. The frontend
    renders an em dash for None. This mirrors how kpi_trends() already
    omits a percentage it cannot honestly compute rather than printing
    a placeholder.
    """
    labels = fleet_status.get("labels") or []
    data = fleet_status.get("data") or []
    counts = dict(zip(labels, data))
    # DISPOSED units have left the fleet; they are not "unavailable".
    denominator = sum(v for k, v in counts.items() if k != "DISPOSED")
    if denominator <= 0:
        return None
    return round(counts.get("ACTIVE", 0) / denominator * 100, 1)



# ── Shared PM due calculation (serialized, short TTL) ───────────────────────
# Parallel dashboard tabs each used to run get_all_due_vehicles() separately
# (~seconds on a full schedule catalogue). Request-scoped cache cannot help
# across concurrent HTTP requests. A short process TTL of *serialized*
# rows is safe (no detached ORM) and collapses the three callers into one
# computation for a few seconds of dashboard paint.

import threading
import time

_DUE_LOCK = threading.Lock()
_DUE_CACHE = {}  # key -> (expires_at, rows)
_DUE_TTL_SEC = 12


def _shared_due_rows(api_user, branch_id=None):
    """DUE_SOON / OVERDUE rows visible to api_user, as plain dicts."""
    from app.core.maintenance.due_calculation_service import (
        PMDueCalculationService)
    from app.modules.user_management.org_scope_service import (
        UserOrgScopeService)

    key = (getattr(api_user, "id", None), branch_id)
    now = time.time()
    with _DUE_LOCK:
        hit = _DUE_CACHE.get(key)
        if hit and hit[0] > now:
            return hit[1]

    due = PMDueCalculationService().get_all_due_vehicles()
    if branch_id is not None:
        due = [d for d in due if d["vehicle"].branch_id == branch_id]
    scope_svc = UserOrgScopeService()
    due = [d for d in due
           if scope_svc.covers(api_user.id, branch_id=d["vehicle"].branch_id)]
    order = {"OVERDUE": 0, "DUE_SOON": 1}
    due.sort(key=lambda d: order.get(d.get("status"), 2))

    rows = []
    for d in due:
        v = d["vehicle"]
        schedule = d.get("schedule")
        m_type = getattr(schedule, "maintenance_type", None) if schedule else None
        due_date = d.get("next_due_date")
        rows.append({
            "vehicle_id": v.id,
            "plate_number": v.plate_number,
            "conduction_number": v.conduction_number,
            "vehicle": f"{v.brand or ''} {v.model or ''}".strip(),
            "branch": v.branch.name if v.branch else None,
            "branch_id": v.branch_id,
            "maintenance_type": getattr(m_type, "name", None),
            "current_odometer": v.current_odometer,
            "due_odometer": d.get("next_due_km"),
            "due_date": due_date.isoformat() if due_date else None,
            "status": d.get("status"),
        })

    with _DUE_LOCK:
        _DUE_CACHE[key] = (now + _DUE_TTL_SEC, rows)
        # Bound cache size
        if len(_DUE_CACHE) > 64:
            expired = [k for k, (exp, _) in _DUE_CACHE.items() if exp <= now]
            for k in expired:
                _DUE_CACHE.pop(k, None)
    return rows


@bp.route("/dashboard/summary", methods=["GET"])
@api_auth_required("vehicle.view")
def dashboard_summary(api_user):
    branch_id, error = _requested_branch_id(api_user)
    if error:
        return error

    dash = DashboardService()
    analytics = DashboardAnalyticsService()
    fleet_status = analytics.fleet_by_status(user=api_user, branch_id=branch_id)
    # Shared with /due-maintenance — one due pass, not two.
    due_rows = _shared_due_rows(api_user, branch_id)

    return jsonify({
        "fleet_count": dash.fleet_count(user=api_user, branch_id=branch_id),
        "maintenance_due_count": len(due_rows),
        "approvals_pending_count": dash.approvals_pending_count(api_user),
        "registrations_expiring_count": dash.registrations_expiring_count(
            user=api_user, branch_id=branch_id),
        "tire_stock_count": dash.tire_stock_count(
            user=api_user, branch_id=branch_id),
        "battery_stock_count": dash.battery_stock_count(
            user=api_user, branch_id=branch_id),
        "availability_percentage": _availability(fleet_status),
        "branch_id": branch_id,
    })


@bp.route("/dashboard/fleet-status", methods=["GET"])
@api_auth_required("vehicle.view")
def dashboard_fleet_status(api_user):
    """Vehicle counts by status.

    Labels are the RAW backend enum values (ACTIVE / IN_REPAIR /
    INACTIVE / DISPOSED), not display text. Translating them here would
    put a second vocabulary in the system and make the API's meaning
    depend on the frontend's copy decisions; the React layer owns its
    own labels and maps from these.
    """
    branch_id, error = _requested_branch_id(api_user)
    if error:
        return error
    return jsonify(DashboardAnalyticsService().fleet_by_status(
        user=api_user, branch_id=branch_id))


@bp.route("/dashboard/charts", methods=["GET"])
@api_auth_required("vehicle.view")
def dashboard_charts(api_user):
    """The analytics charts, in the same {labels, data, colors} shape the
    Jinja dashboard's Chart.js already consumes.

    ?only=a,b limits the payload, matching the existing web route. This
    matters more than it looks: the six charts have wildly different
    costs -- registration_status is a simple aggregate, pm_compliance
    runs the full fleet-wide due calculation -- so a client that wants
    the cheap ones should not have to wait for the expensive one.
    """
    only = request.args.get("only")
    only = [c.strip() for c in only.split(",") if c.strip()] if only else None
    branch_id, error = _requested_branch_id(api_user)
    if error:
        return error
    return jsonify(DashboardAnalyticsService().all_charts(
        user=api_user, only=only, branch_id=branch_id))


@bp.route("/dashboard/fuel", methods=["GET"])
@api_auth_required("fuel.view")
def dashboard_fuel(api_user):
    """Fuel management summary.

    Gated on `fuel.view`, NOT `vehicle.view`: the Jinja dashboard hides
    this panel behind that permission, and an API that answered on the
    weaker one would be a way around the very gate the UI is enforcing.

    Decimal values (litres, spend, avg_price) serialise as JSON strings
    rather than floats. Binary floating point cannot represent most
    decimal fractions exactly, and these are pesos and litres the client
    reconciles against fuel receipts -- a cent of drift per row is a
    support ticket. The frontend formats from the string.

    avg_kmpl is null, never 0, when nothing is measurable: zero km/L
    would read as catastrophic efficiency rather than "no fills yet".
    """
    from app.modules.transactions.fuel.analytics import FuelAnalyticsService

    try:
        days = int(request.args.get("days", 30))
    except (TypeError, ValueError):
        return jsonify({"error": "bad_request",
                        "message": "days must be an integer."}), 400
    days = max(1, min(days, 365))
    return jsonify(FuelAnalyticsService().summary(user=api_user, days=days))


@bp.route("/dashboard/workflow", methods=["GET"])
@api_auth_required("vehicle.view")
def dashboard_workflow(api_user):
    return jsonify(DashboardAnalyticsService().approval_workflow_counts(
        user=api_user))


@bp.route("/dashboard/trends", methods=["GET"])
@api_auth_required("vehicle.view")
def dashboard_trends(api_user):
    """Month-over-month KPI movement and sparkline series.

    Keys are ABSENT where no honest comparison exists (no history, or a
    prior count of zero). The frontend must treat a missing key as "no
    trend to show" and render the card without a trend row -- not as
    zero, and not as flat.
    """
    return jsonify(DashboardAnalyticsService().kpi_trends(user=api_user))


@bp.route("/dashboard/due-maintenance", methods=["GET"])
@api_auth_required("vehicle.view")
def dashboard_due_maintenance(api_user):
    """Vehicles that are DUE_SOON or OVERDUE for preventive maintenance.

    Includes IN_REPAIR vehicles: the underlying
    DUE_ELIGIBLE_STATUSES = ("ACTIVE", "IN_REPAIR") is a confirmed client
    requirement -- a unit in the shop is precisely one that maintenance
    still applies to, and dropping it would hide work in hand.
    """
    branch_id, error = _requested_branch_id(api_user)
    if error:
        return error

    try:
        limit = int(request.args.get("limit", 20))
    except (TypeError, ValueError):
        return jsonify({"error": "bad_request",
                        "message": "limit must be an integer."}), 400
    limit = max(1, min(limit, 200))

    rows = _shared_due_rows(api_user, branch_id)
    items = rows[:limit]
    return jsonify({"items": items, "total": len(rows)})


@bp.route("/dashboard/branches", methods=["GET"])
@api_auth_required("vehicle.view")
def dashboard_branches(api_user):
    """Branches this user may filter by.

    The frontend's branch dropdown is populated from here rather than
    from a list in the React source, so it can never offer a branch the
    API would then refuse -- and so adding a branch in Master Data
    appears in the filter with no frontend deploy.
    """
    from app.modules.master_data.org.models import Branch
    from app.modules.user_management.org_scope_service import (
        UserOrgScopeService)

    scope_svc = UserOrgScopeService()
    branches = Branch.query.filter_by(is_active=True).order_by(
        Branch.name).all()
    return jsonify({"items": [
        {"id": b.id, "code": b.code, "name": b.name}
        for b in branches
        if scope_svc.covers(api_user.id, branch_id=b.id)]})


@bp.route("/dashboard/awaiting-approval", methods=["GET"])
@api_auth_required("vehicle.view")
def dashboard_awaiting_approval(api_user):
    """The signed-in user's pending approval queue.

    Deliberately built from the SAME pieces as the Jinja "For My Action"
    worklist -- ApprovalTaskService.list_for_user() for the queue, and
    the reference_resolver for the plate and type label. The two UIs show
    one queue; if they disagreed about which documents are waiting, an
    approver would have no way to tell which to believe.

    No branch_id: approval tasks scope by APPROVER, not by branch.
    list_for_user() already applies the user's org scope internally when
    matching role-assigned tasks, so a branch filter here would be either
    redundant or wrong.
    """
    try:
        limit = int(request.args.get("limit", 5))
    except (TypeError, ValueError):
        return jsonify({"error": "bad_request",
                        "message": "limit must be an integer."}), 400
    limit = max(1, min(limit, 100))
    try:
        offset = max(0, int(request.args.get("offset", 0)))
    except (TypeError, ValueError):
        return jsonify({"error": "bad_request",
                        "message": "offset must be an integer."}), 400

    from app.core.approval.task_service import ApprovalTaskService
    from app.core.reference_resolver import (get_worklist_labels,
                                             get_document_number)
    from app.modules.user_management.models import User

    tasks = ApprovalTaskService().list_for_user(api_user)
    total = len(tasks)
    window = tasks[offset:offset + limit]

    items = []
    for t in window:
        labels = get_worklist_labels(t.reference_table, t.reference_id)

        # ApprovalTask.document_number is a DENORMALISED copy taken at
        # submit time. If auto-numbering assigned the number later, that
        # copy stays null forever even though the document now has one --
        # which is why a real MO once rendered as "(no number)" here
        # while the Maintenance Orders list showed MO-2026-000020.
        # Resolving against the live record makes every task already in
        # the queue self-correcting, with no data migration. The Jinja
        # worklist does exactly this; diverging would put two different
        # numbers on screen for one document.
        document_number = t.document_number
        if not document_number:
            resolved = get_document_number(t.reference_table, t.reference_id)
            # get_document_number() falls back to "<table> #<id>" when
            # there genuinely is no number; that is noise on a dashboard.
            document_number = (
                resolved
                if resolved and not resolved.startswith(t.reference_table)
                else None)

        requester = (db_session_get(User, t.requested_by)
                     if t.requested_by else None)

        items.append({
            "task_id": t.id,
            "document_number": document_number,
            "document_type": (t.document_type.name
                              if getattr(t, "document_type", None) else None),
            "plate_number": labels.get("plate_number"),
            "type_label": labels.get("type_label"),
            "level_number": t.level_number,
            "assigned_to": (t.assigned_role.name
                            if getattr(t, "assigned_role", None) else None),
            "requested_by": _person_label(requester),
            "created_at": t.created_at.isoformat() if t.created_at else None,
            "reference_table": t.reference_table,
            "reference_id": t.reference_id,
        })

    return jsonify({"items": items, "total": total, "offset": offset})


def db_session_get(model, pk):
    from app.extensions import db
    return db.session.get(model, pk) if pk else None


def _person_label(user):
    """'R. Delgado' style: initial + surname, matching the worklist.

    Falls back to the username when names are absent -- an approver
    needs to know WHO raised a document, and a blank is worse than a
    less friendly identifier.
    """
    if user is None:
        return None
    first = (user.first_name or "").strip()
    last = (user.last_name or "").strip()
    if first and last:
        return f"{first[0]}. {last}"
    return last or first or user.username


@bp.route("/dashboard/due-registration", methods=["GET"])
@api_auth_required("vehicle.view")
def dashboard_due_registration(api_user):
    """Vehicles whose LTO registration is expiring or expired.

    Uses RegistrationDueCalculationService -- the same service behind the
    Registrations KPI card and the Jinja widget. That is not incidental:
    the card and the list are one population, and they have already
    disagreed once on this project (card 0, list 1) because a count and
    a list used different lookups. A test asserts `total` equals
    registrations_expiring_count so they cannot drift apart again.

    Vehicles with an open Vehicle Registration already in flight are
    excluded by the service: the renewal has been raised, so listing it
    again double-counts it and sends someone to raise a duplicate.
    """
    branch_id, error = _requested_branch_id(api_user)
    if error:
        return error

    try:
        limit = int(request.args.get("limit", 5))
        offset = max(0, int(request.args.get("offset", 0)))
    except (TypeError, ValueError):
        return jsonify({"error": "bad_request",
                        "message": "limit and offset must be integers."}), 400
    limit = max(1, min(limit, 200))

    from app.modules.registration_config.service import (
        RegistrationDueCalculationService)
    from app.modules.user_management.org_scope_service import (
        UserOrgScopeService)

    due = RegistrationDueCalculationService().get_all_due_vehicles()
    if branch_id is not None:
        due = [d for d in due if d["vehicle"].branch_id == branch_id]
    scope_svc = UserOrgScopeService()
    due = [d for d in due
           if scope_svc.covers(api_user.id, branch_id=d["vehicle"].branch_id)]

    # Soonest expiry first, and OVERDUE ahead of DUE_SOON. A `limit` must
    # truncate the least urgent rows, never the expired ones.
    def _sort_key(d):
        date = d.get("next_due_date")
        return (0 if d.get("status") == "OVERDUE" else 1,
                date or _date.max)

    due.sort(key=_sort_key)
    total = len(due)

    items = []
    for d in due[offset:offset + limit]:
        v = d["vehicle"]
        expiry = d.get("next_due_date")
        items.append({
            "vehicle_id": v.id,
            "plate_number": v.plate_number,
            "conduction_number": v.conduction_number,
            "vehicle": f"{v.brand} {v.model}".strip(),
            "branch": v.branch.name if v.branch else None,
            "status": d.get("status"),
            "expiry_date": expiry.isoformat() if expiry else None,
            # Negative means expired. Comes from the service rather than
            # being recomputed here, so the figure matches the Jinja
            # widget exactly.
            "days_remaining": d.get("days_remaining"),
        })

    return jsonify({"items": items, "total": total, "offset": offset})
