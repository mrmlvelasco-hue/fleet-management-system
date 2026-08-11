"""Landing dashboard — Phase 4. KPI cards show real, org-scope-aware
counts; the "For My Action" widget (F2/F3) is the generic Approval Task
inbox; the "Vehicles Due for Maintenance" widget surfaces PM due/overdue
vehicles, also org-scope aware."""
from datetime import datetime, timezone

from flask import Blueprint, jsonify, render_template, url_for
from flask_login import login_required, current_user

from app.core.approval.task_service import ApprovalTaskService
from app.core.approval.task_url_resolver import resolve_task_url
from app.core.dashboard_service import DashboardService

bp = Blueprint("main", __name__, template_folder="templates")


def _aging_label(created_at) -> str:
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - created_at
    days = delta.days
    if days >= 1:
        return f"{days} day{'s' if days != 1 else ''} waiting"
    hours = delta.seconds // 3600
    if hours >= 1:
        return f"{hours} hour{'s' if hours != 1 else ''} waiting"
    return "Just now"


_DEFAULT_WIDGET_CODES = {"FLEET", "MAINTENANCE", "APPROVALS", "REGISTRATIONS",
                        "TIRES", "BATTERIES"}


def _visible_widget_codes(user) -> set:
    """A widget is shown unless the user has an explicit is_visible=False
    config row for it — falls back to DashboardWidget.default_visible when
    no per-user config exists (same "unconfigured = default" convention
    used throughout this project). If `flask seed all` hasn't run yet
    (DashboardWidget table empty), falls back to the code-registered
    defaults so the dashboard never renders blank on a fresh install."""
    from app.modules.system_admin.models import DashboardWidget, UserDashboardConfig
    widgets = DashboardWidget.query.order_by(DashboardWidget.sort_order).all()
    if not widgets:
        return set(_DEFAULT_WIDGET_CODES)
    configs = {c.widget_code: c.is_visible
              for c in UserDashboardConfig.query.filter_by(user_id=user.id).all()}
    return {w.code for w in widgets
           if configs.get(w.code, w.default_visible)}


def _widget_exists(code: str) -> bool:
    """True if a DashboardWidget row exists for `code`. Used so panels
    (My Actions, Vehicle List, due tables) that pre-date the widget
    catalog stay visible on installs where `flask seed all` hasn't yet
    added their catalog rows — "unregistered widget = show it" preserves
    backward compatibility."""
    from app.modules.system_admin.models import DashboardWidget
    return DashboardWidget.query.filter_by(code=code).first() is not None


_PANEL_WIDGET_CODES = ("REGISTRATION_STATUS", "MAINTENANCE_MANAGEMENT",
                       "APPROVAL_WORKFLOW", "SECURITY_COMPLIANCE",
                       "FUEL_MANAGEMENT", "ANALYTICS")


def _panel_visibility(visible_codes: set) -> dict:
    """{widget_code: bool} for the panels added to the dashboard after
    the widget catalog was first written -- Registration Status,
    Maintenance Management, Approval Workflow, Security and Compliance,
    Fuel Management, and Analytics. Every one of these was always fully
    visible with no way to turn it off from Customize until these
    catalog rows existed. Same "unregistered = show it" rule as
    _widget_exists() everywhere else in this file, so an install that
    hasn't re-run `flask seed all` yet doesn't lose a panel it always
    had."""
    return {code: (code in visible_codes or not _widget_exists(code))
           for code in _PANEL_WIDGET_CODES}


@bp.route("/")
@login_required
def dashboard():
    dash = DashboardService()
    visible_codes = _visible_widget_codes(current_user)

    # The Maintenance and Registration figures each require a fleet-wide
    # PM/registration evaluation. That work does not grow with the page
    # -- it grows with the FLEET -- so computing it before the first byte
    # is sent means the browser cannot paint ANYTHING until it finishes.
    # That is why the measured LCP element was `span.sidebar-label`: even
    # the sidebar text was waiting on a database calculation it has
    # nothing to do with.
    #
    # Those two cards (and the two tables below) are therefore marked
    # `deferred` and filled in by a follow-up request. The shell,
    # navigation and the cheap COUNT(*) cards render immediately.
    all_cards = [
        {"code": "FLEET", "title": "Fleet", "icon": "bi-truck",
         "value": dash.fleet_count(user=current_user),
         "url": url_for("master_data.vehicle_list")},
        {"code": "MAINTENANCE", "title": "Maintenance", "icon": "bi-wrench",
         "value": None, "deferred": True,
         "url": url_for("transactions.maintenanceorder_list")},
        {"code": "APPROVALS", "title": "Approvals", "icon": "bi-check2-square",
         "value": dash.approvals_pending_count(current_user),
         "url": "#for-my-action"},
        {"code": "REGISTRATIONS", "title": "Registrations", "icon": "bi-card-checklist",
         "value": None, "deferred": True,
         "url": url_for("transactions.vehicleregistration_list")},
        {"code": "TIRES", "title": "Tires", "icon": "bi-circle",
         "value": dash.tire_stock_count(user=current_user),
         "url": url_for("master_data.tire_list")},
        {"code": "BATTERIES", "title": "Batteries", "icon": "bi-battery-half",
         "value": dash.battery_stock_count(user=current_user),
         "url": url_for("master_data.battery_list")},
    ]
    cards = [c for c in all_cards if c["code"] in visible_codes]

    # "For My Action" panel — now a toggleable widget (MY_ACTIONS). Defaults
    # to shown if the widget row is absent, so existing installs are
    # unaffected.
    for_my_action = []
    if "MY_ACTIONS" in visible_codes or not _widget_exists("MY_ACTIONS"):
        my_tasks = ApprovalTaskService().list_for_user(current_user)
        from app.core.reference_resolver import (get_worklist_labels,
                                                 get_document_number)
        for_my_action = []
        for t in my_tasks:
            labels = get_worklist_labels(t.reference_table, t.reference_id)
            # ApprovalTask.document_number is a DENORMALISED copy taken
            # at submit time. If the source record had no number yet at
            # that moment (auto-numbering can fail or be assigned later),
            # the copy stays null forever even once the document does
            # have a number -- which is exactly why a real MO showed as
            # "(no number)" here while the Maintenance Orders list showed
            # MO-2026-000020. Falling back to the LIVE record makes the
            # display self-correcting for every task already in the
            # queue, with no data migration needed.
            document_number = t.document_number
            if not document_number:
                resolved = get_document_number(t.reference_table,
                                              t.reference_id)
                # get_document_number() returns a "<table> #<id>" style
                # fallback when there genuinely is no number; that's
                # noise on a dashboard, so keep the friendlier text.
                document_number = (resolved
                                  if resolved and not resolved.startswith(
                                      t.reference_table)
                                  else "(no number)")
            for_my_action.append({
                "document_number": document_number,
                "document_type": t.document_type.name if t.document_type else "",
                "plate_number": labels["plate_number"],
                "type_label": labels["type_label"],
                "activity": labels.get("activity"),
                "requester": t.requester.full_name if t.requester else "Unknown",
                "created_at": t.created_at,
                "aging": _aging_label(t.created_at),
                "level_number": t.level_number,
                "url": resolve_task_url(t),
                "reference_table": t.reference_table,
            })

    # "Vehicle List" panel (VEHICLE_LIST widget) — the compact recent-fleet
    # table the client asked to have as a dashboard option.
    recent_vehicles = []
    if "VEHICLE_LIST" in visible_codes:
        recent_vehicles = [{
            "vehicle": v,
            "url": url_for("master_data.vehicle_detail", vid=v.id),
        } for v in dash.recent_vehicles(user=current_user, limit=10)]

    # ── "For My Action" tiles ────────────────────────────────────────
    # The mockup shows this as one tile per module with a count badge and
    # a View button, rather than a flat list of individual documents --
    # an approver wants to see "18 Purchase Requests" and go there, not
    # scroll 18 rows on a dashboard.
    #
    # Built by grouping the SAME worklist the list below uses, so the
    # tile counts can never disagree with what the person actually finds
    # when they click through.
    _TILE_META = {
        "maintenance_orders": ("Maintenance Orders", "bi-wrench-adjustable",
                              "transactions.maintenanceorder_list"),
        "purchase_requests": ("Purchase Requests", "bi-cart",
                             "transactions.purchaserequest_list"),
        "authority_to_drive": ("ATD Requests", "bi-person-badge",
                              "transactions.atd_list"),
        "vehicle_movements": ("Vehicle Movements", "bi-arrow-left-right",
                             "transactions.vehiclemovement_list"),
        "vehicle_registrations": ("Registration Renewals", "bi-card-checklist",
                                 "transactions.vehicleregistration_list"),
        "trip_tickets": ("Trip Tickets", "bi-ticket-detailed",
                        "transactions.tripticket_list"),
    }
    _counts = {}
    for item in for_my_action:
        _counts[item["reference_table"]] = _counts.get(
            item["reference_table"], 0) + 1

    action_tiles = []
    for table, count in sorted(_counts.items(), key=lambda kv: -kv[1]):
        label, icon, endpoint = _TILE_META.get(
            table, (table.replace("_", " ").title(), "bi-inbox", None))
        try:
            url = url_for(endpoint) if endpoint else "#for-my-action-list"
        except Exception:
            # An unmapped or renamed module must not 500 the whole
            # dashboard -- fall back to the detailed list below.
            url = "#for-my-action-list"
        action_tiles.append({"label": label, "icon": icon,
                            "count": count, "url": url})

    # Computed asynchronously -- see dashboard_widgets() below.
    due_vehicles = None
    due_registrations = None

    # Workflow counts and KPI trends are cheap aggregate COUNT(*) queries,
    # so unlike the fleet-wide due calculations they can stay inline
    # without delaying first paint.
    from app.core.dashboard_analytics_service import DashboardAnalyticsService
    _analytics = DashboardAnalyticsService()

    # Cheap bounded aggregate (30-day window, simple SUM/COUNT) -- unlike
    # the PM/registration due calculations this doesn't need to be
    # deferred to the async widgets endpoint. Gated so the query doesn't
    # run at all for a user without fuel.view, OR one who has hidden
    # this panel from Customize.
    fuel_summary = None
    panel_visible = _panel_visibility(visible_codes)
    if current_user.has_permission("fuel.view") and panel_visible["FUEL_MANAGEMENT"]:
        from app.modules.transactions.fuel.analytics import FuelAnalyticsService
        fuel_summary = FuelAnalyticsService().summary(user=current_user)

    return render_template("main/dashboard.html", cards=cards,
                           for_my_action=for_my_action,
                           action_tiles=action_tiles,
                           recent_vehicles=recent_vehicles,
                           due_vehicles=due_vehicles,
                           due_registrations=due_registrations,
                           workflow=_analytics.approval_workflow_counts(
                               user=current_user),
                           kpi_trends=_analytics.kpi_trends(user=current_user),
                           fuel_summary=fuel_summary,
                           panel_visible=panel_visible)


def _compute_due_widgets(user):
    """The two fleet-wide calculations, factored out of the page route.

    Kept here rather than inlined in the endpoint so the page route and
    the async endpoint can never drift apart in what they produce.
    """
    visible_codes = _visible_widget_codes(user)
    due_vehicles = []
    if ("DUE_MAINTENANCE" in visible_codes
            or (not _widget_exists("DUE_MAINTENANCE")
                and "MAINTENANCE" in visible_codes)):
        from app.core.maintenance.due_calculation_service import (
            PMDueCalculationService)
        from app.modules.user_management.org_scope_service import (
            UserOrgScopeService)
        scope_svc = UserOrgScopeService()
        can_create_mo = user.has_permission("maintenanceorder.create")
        for d in PMDueCalculationService().get_all_due_vehicles():
            vehicle = d["vehicle"]
            if not scope_svc.covers(user.id, branch_id=vehicle.branch_id):
                continue
            if can_create_mo:
                # Link straight into a ready-to-submit Maintenance Order
                # instead of just the vehicle's detail page — the vehicle,
                # matched maintenance type, and current odometer are all
                # already known, so pre-fill them.
                link_url = url_for(
                    "transactions.maintenanceorder_new",
                    vehicle_id=vehicle.id,
                    maintenance_type_id=d["schedule"].maintenance_type_id,
                    odometer_at_service=vehicle.current_odometer,
                    scheduled_date=datetime.now().date().isoformat())
            else:
                link_url = url_for("master_data.vehicle_detail", vid=vehicle.id)
            due_vehicles.append({
                "vehicle": vehicle,
                "maintenance_type": (d["schedule"].maintenance_type.name
                                    if d.get("schedule")
                                    and d["schedule"].maintenance_type
                                    else "—"),
                "status": d["status"],
                "next_due_km": d["next_due_km"],
                "next_due_date": d["next_due_date"],
                "url": link_url,
            })

    due_registrations = []
    if ("DUE_REGISTRATION" in visible_codes
            or (not _widget_exists("DUE_REGISTRATION")
                and "REGISTRATIONS" in visible_codes)):
        from app.modules.registration_config.service import (
            RegistrationDueCalculationService)
        from app.modules.user_management.org_scope_service import (
            UserOrgScopeService)
        scope_svc = UserOrgScopeService()
        can_create_vr = user.has_permission("vehicleregistration.create")
        for d in RegistrationDueCalculationService().get_all_due_vehicles():
            vehicle = d["vehicle"]
            if not scope_svc.covers(user.id, branch_id=vehicle.branch_id):
                continue
            if can_create_vr:
                # Same pre-fill pattern as Maintenance's due-vehicles link
                # — straight into a ready-to-submit renewal.
                link_url = url_for(
                    "transactions.vehicleregistration_new",
                    vehicle_id=vehicle.id, registration_type="RENEWAL",
                    registration_date=datetime.now().date().isoformat())
            else:
                link_url = url_for("master_data.vehicle_detail", vid=vehicle.id)
            due_registrations.append({
                "vehicle": vehicle,
                "status": d["status"],
                "next_due_date": d["next_due_date"],
                "url": link_url,
            })

    return due_vehicles, due_registrations


@bp.route("/dashboard/charts")
@login_required
def dashboard_charts():
    """Chart data for the dashboard's analytics row and the standalone
    Analytics page -- ONE endpoint, so both places can never show
    different numbers for the same chart.

    Fetched asynchronously for the same reason the due-maintenance
    widgets are: several of these run an aggregate query over the whole
    fleet, and that cost must not sit in front of the page's first
    paint."""
    from app.core.dashboard_analytics_service import DashboardAnalyticsService
    return jsonify(DashboardAnalyticsService().all_charts(user=current_user))


@bp.route("/dashboard/widgets")
@login_required
def dashboard_widgets():
    """Serves the parts of the dashboard whose cost scales with FLEET
    SIZE, so the page itself never waits on them.

    Returning rendered HTML rather than raw JSON keeps a single source of
    truth for the markup -- the same partials the page would have used --
    instead of duplicating the table layout in JavaScript.
    """
    dash = DashboardService()
    due_vehicles, due_registrations = _compute_due_widgets(current_user)
    return jsonify({
        "maintenance_count": len(due_vehicles),
        "registration_count": len(due_registrations),
        "due_maintenance_html": render_template(
            "main/_due_maintenance.html", due_vehicles=due_vehicles),
        "due_registration_html": render_template(
            "main/_due_registration.html", due_registrations=due_registrations),
    })
