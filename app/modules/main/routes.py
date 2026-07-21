"""Landing dashboard — Phase 4. KPI cards show real, org-scope-aware
counts; the "For My Action" widget (F2/F3) is the generic Approval Task
inbox; the "Vehicles Due for Maintenance" widget surfaces PM due/overdue
vehicles, also org-scope aware."""
from datetime import datetime, timezone

from flask import Blueprint, render_template, url_for
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


@bp.route("/")
@login_required
def dashboard():
    dash = DashboardService()
    visible_codes = _visible_widget_codes(current_user)

    all_cards = [
        {"code": "FLEET", "title": "Fleet", "icon": "bi-truck",
         "value": dash.fleet_count(user=current_user),
         "url": url_for("master_data.vehicle_list")},
        {"code": "MAINTENANCE", "title": "Maintenance", "icon": "bi-wrench",
         "value": dash.maintenance_due_count(user=current_user),
         "url": url_for("transactions.maintenanceorder_list")},
        {"code": "APPROVALS", "title": "Approvals", "icon": "bi-check2-square",
         "value": dash.approvals_pending_count(current_user),
         "url": "#for-my-action"},
        {"code": "REGISTRATIONS", "title": "Registrations", "icon": "bi-card-checklist",
         "value": dash.registrations_expiring_count(user=current_user),
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
        from app.core.reference_resolver import get_worklist_labels
        for_my_action = []
        for t in my_tasks:
            labels = get_worklist_labels(t.reference_table, t.reference_id)
            for_my_action.append({
                "document_number": t.document_number or "(no number)",
                "document_type": t.document_type.name if t.document_type else "",
                "plate_number": labels["plate_number"],
                "type_label": labels["type_label"],
                "requester": t.requester.full_name if t.requester else "Unknown",
                "created_at": t.created_at,
                "aging": _aging_label(t.created_at),
                "level_number": t.level_number,
                "url": resolve_task_url(t),
            })

    # "Vehicle List" panel (VEHICLE_LIST widget) — the compact recent-fleet
    # table the client asked to have as a dashboard option.
    recent_vehicles = []
    if "VEHICLE_LIST" in visible_codes:
        recent_vehicles = [{
            "vehicle": v,
            "url": url_for("master_data.vehicle_detail", vid=v.id),
        } for v in dash.recent_vehicles(user=current_user, limit=10)]

    due_vehicles = []
    if ("DUE_MAINTENANCE" in visible_codes
            or (not _widget_exists("DUE_MAINTENANCE")
                and "MAINTENANCE" in visible_codes)):
        from app.core.maintenance.due_calculation_service import (
            PMDueCalculationService)
        from app.modules.user_management.org_scope_service import (
            UserOrgScopeService)
        scope_svc = UserOrgScopeService()
        can_create_mo = current_user.has_permission("maintenanceorder.create")
        for d in PMDueCalculationService().get_all_due_vehicles():
            vehicle = d["vehicle"]
            if not scope_svc.covers(current_user.id, branch_id=vehicle.branch_id):
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
        can_create_vr = current_user.has_permission("vehicleregistration.create")
        for d in RegistrationDueCalculationService().get_all_due_vehicles():
            vehicle = d["vehicle"]
            if not scope_svc.covers(current_user.id, branch_id=vehicle.branch_id):
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

    return render_template("main/dashboard.html", cards=cards,
                           for_my_action=for_my_action,
                           recent_vehicles=recent_vehicles,
                           due_vehicles=due_vehicles,
                           due_registrations=due_registrations)
