"""PM Configuration blueprint: PM Schedules, PM Scope Templates.
Thin controllers — business logic in services."""
from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required

from app.core.security.decorators import require_permission
from app.core.security.registry import registry
from app.modules.maintenance_config.service import (
    PMScheduleService, PMScopeTemplateService,
    InvalidScheduleError, InvalidScopeError)
from app.modules.master_data.reference.service import (
    VehicleTypeService, MaintenanceTypeService)

bp = Blueprint("maintenance_config", __name__, url_prefix="/admin",
               template_folder="templates")

for _code, _desc in [
    ("pmschedule.view", "View PM schedules"),
    ("pmschedule.create", "Create PM schedules"),
    ("pmschedule.update", "Update PM schedules"),
    ("pmschedule.delete", "Deactivate PM schedules"),
    ("pmscopetemplate.view", "View PM scope templates"),
    ("pmscopetemplate.create", "Create PM scope templates"),
    ("pmscopetemplate.update", "Update PM scope templates"),
    ("pmscopetemplate.delete", "Deactivate PM scope templates"),
]:
    _m, _a = _code.split(".")
    registry.register(_code, _m, _a, _desc)


# ── PM Schedules ─────────────────────────────────────────────────────────

@bp.route("/pm-schedules")
@login_required
@require_permission("pmschedule.view")
def pmschedule_list():
    items = PMScheduleService().list(include_inactive=True)
    return render_template("maintenance_config/schedule_list.html", items=items)


@bp.route("/pm-schedules/new", methods=["GET", "POST"])
@login_required
@require_permission("pmschedule.create")
def pmschedule_new():
    vehicle_types = VehicleTypeService().list()
    maintenance_types = MaintenanceTypeService().list()
    if request.method == "POST":
        f = request.form
        try:
            PMScheduleService().create(
                vehicle_type_id=int(f["vehicle_type_id"]) if f.get("vehicle_type_id") else None,
                maintenance_type_id=int(f["maintenance_type_id"]),
                trigger_mode=f["trigger_mode"],
                interval_km=int(f["interval_km"]) if f.get("interval_km") else None,
                interval_days=int(f["interval_days"]) if f.get("interval_days") else None,
                priority=f.get("priority", "MEDIUM"))
            flash("PM Schedule created.", "success")
            return redirect(url_for("maintenance_config.pmschedule_list"))
        except InvalidScheduleError as e:
            flash(str(e), "danger")
    return render_template("maintenance_config/schedule_form.html",
                           vehicle_types=vehicle_types,
                           maintenance_types=maintenance_types,
                           title="New PM Schedule")


@bp.route("/pm-schedules/<int:sid>/deactivate", methods=["POST"])
@login_required
@require_permission("pmschedule.delete")
def pmschedule_deactivate(sid):
    PMScheduleService().deactivate(sid)
    flash("PM Schedule deactivated.", "info")
    return redirect(url_for("maintenance_config.pmschedule_list"))


# ── PM Scope Templates ───────────────────────────────────────────────────

@bp.route("/pm-scope-templates")
@login_required
@require_permission("pmscopetemplate.view")
def pmscope_list():
    items = PMScopeTemplateService().list(include_inactive=True)
    return render_template("maintenance_config/scope_list.html", items=items)


@bp.route("/pm-scope-templates/new", methods=["GET", "POST"])
@login_required
@require_permission("pmscopetemplate.create")
def pmscope_new():
    maintenance_types = MaintenanceTypeService().list()
    if request.method == "POST":
        f = request.form
        codes = f.getlist("activity_code")
        descs = f.getlist("activity_description")
        items = [{"activity_code": c, "activity_description": d, "sort_order": i + 1}
                 for i, (c, d) in enumerate(zip(codes, descs)) if c and d]
        try:
            PMScopeTemplateService().create(
                maintenance_type_id=int(f["maintenance_type_id"]),
                name=f["name"], description=f.get("description"), items=items)
            flash("PM Scope Template created.", "success")
            return redirect(url_for("maintenance_config.pmscope_list"))
        except InvalidScopeError as e:
            flash(str(e), "danger")
    return render_template("maintenance_config/scope_form.html",
                           maintenance_types=maintenance_types,
                           title="New PM Scope Template")


@bp.route("/pm-scope-templates/<int:tid>/deactivate", methods=["POST"])
@login_required
@require_permission("pmscopetemplate.delete")
def pmscope_deactivate(tid):
    PMScopeTemplateService().deactivate(tid)
    flash("PM Scope Template deactivated.", "info")
    return redirect(url_for("maintenance_config.pmscope_list"))
