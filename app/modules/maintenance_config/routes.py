"""PM Configuration blueprint: PM Schedules, PM Scope Templates.
Thin controllers — business logic in services."""
from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required

from app.core.security.decorators import require_permission
from app.core.security.registry import registry
from app.modules.maintenance_config.service import (
    PMScheduleService, PMScopeTemplateService,
    InvalidScheduleError, InvalidScopeError)
from app.modules.maintenance_config.import_service import (
    PMScheduleImportService, PMScopeImportService)
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
    from app.modules.system_admin.services.lookup_service import LookupService
    from app.modules.master_data.vehicle_brand.service import VehicleBrandService
    from app.core.validation.date_utils import (
        parse_form_date, DateFormatError, RequiredFieldError)
    vehicle_types = VehicleTypeService().list()
    maintenance_types = MaintenanceTypeService().list()
    priorities = LookupService().get_by_type_with_fallback("PM_PRIORITY")
    fuel_types = LookupService().get_by_type_with_fallback("FUEL_TYPE")
    vehicle_brands = VehicleBrandService().list()
    if request.method == "POST":
        f = request.form
        try:
            PMScheduleService().create(
                vehicle_type_id=int(f["vehicle_type_id"]) if f.get("vehicle_type_id") else None,
                vehicle_make=f.get("vehicle_make"),
                vehicle_model=f.get("vehicle_model"),
                vehicle_brand_id=int(f["vehicle_brand_id"]) if f.get("vehicle_brand_id") else None,
                vehicle_model_id=int(f["vehicle_model_id"]) if f.get("vehicle_model_id") else None,
                variant=f.get("variant") or None,
                engine_type=f.get("engine_type") or None,
                fuel_type=f.get("fuel_type") or None,
                transmission=f.get("transmission") or None,
                model_year_from=int(f["model_year_from"]) if f.get("model_year_from") else None,
                model_year_to=int(f["model_year_to"]) if f.get("model_year_to") else None,
                profile_code=f.get("profile_code") or None,
                profile_description=f.get("profile_description") or None,
                effective_date=parse_form_date(f.get("effective_date"), "Effective Date"),
                maintenance_type_id=int(f["maintenance_type_id"]),
                trigger_mode=f["trigger_mode"],
                interval_km=int(f["interval_km"]) if f.get("interval_km") else None,
                interval_days=int(f["interval_days"]) if f.get("interval_days") else None,
                priority=f.get("priority", "MEDIUM"),
                notify_before_km=int(f["notify_before_km"]) if f.get("notify_before_km") else None,
                notify_before_days=int(f["notify_before_days"]) if f.get("notify_before_days") else None,
                escalate_if_overdue=f.get("escalate_if_overdue") == "on")
            flash("PM Template created.", "success")
            return redirect(url_for("maintenance_config.pmschedule_list"))
        except (InvalidScheduleError, DateFormatError, RequiredFieldError) as e:
            flash(str(e), "danger")
    return render_template("maintenance_config/schedule_form.html",
                           vehicle_types=vehicle_types,
                           maintenance_types=maintenance_types,
                           priorities=priorities,
                           fuel_types=fuel_types,
                           vehicle_brands=vehicle_brands,
                           title="New PM Template")


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
    pm_schedules = PMScheduleService().list()
    if request.method == "POST":
        f = request.form
        codes = f.getlist("activity_code")
        descs = f.getlist("activity_description")
        items = [{"activity_code": c, "activity_description": d, "sort_order": i + 1}
                 for i, (c, d) in enumerate(zip(codes, descs)) if c and d]
        try:
            PMScopeTemplateService().create(
                maintenance_type_id=int(f["maintenance_type_id"]),
                pm_schedule_id=int(f["pm_schedule_id"]) if f.get("pm_schedule_id") else None,
                name=f["name"], description=f.get("description"), items=items)
            flash("PM Scope Template created.", "success")
            return redirect(url_for("maintenance_config.pmscope_list"))
        except InvalidScopeError as e:
            flash(str(e), "danger")
    return render_template("maintenance_config/scope_form.html",
                           maintenance_types=maintenance_types,
                           pm_schedules=pm_schedules,
                           title="New PM Scope Template")


@bp.route("/pm-scope-templates/<int:tid>/deactivate", methods=["POST"])
@login_required
@require_permission("pmscopetemplate.delete")
def pmscope_deactivate(tid):
    PMScopeTemplateService().deactivate(tid)
    flash("PM Scope Template deactivated.", "info")
    return redirect(url_for("maintenance_config.pmscope_list"))


# ── CSV Bulk Import ──────────────────────────────────────────────────────

@bp.route("/pm-schedules/import", methods=["GET", "POST"])
@login_required
@require_permission("pmschedule.create")
def pmschedule_import():
    result = None
    if request.method == "POST":
        file = request.files.get("csv_file")
        if file and file.filename:
            import io
            content = file.read().decode("utf-8-sig")
            result = PMScheduleImportService().import_csv(io.StringIO(content))
            if result["created"]:
                flash(f"Imported {result['created']} PM schedule(s).", "success")
            if result["errors"]:
                flash(f"{len(result['errors'])} row(s) had errors — see below.",
                     "warning")
        else:
            flash("Please choose a CSV file.", "danger")
    return render_template("maintenance_config/schedule_import.html",
                           result=result)


@bp.route("/pm-scope-templates/import", methods=["GET", "POST"])
@login_required
@require_permission("pmscopetemplate.create")
def pmscope_import():
    result = None
    if request.method == "POST":
        file = request.files.get("csv_file")
        if file and file.filename:
            import io
            content = file.read().decode("utf-8-sig")
            result = PMScopeImportService().import_csv(io.StringIO(content))
            if result["templates_created"] or result["items_created"]:
                flash(f"Imported {result['templates_created']} template(s), "
                     f"{result['items_created']} activity item(s).", "success")
            if result["errors"]:
                flash(f"{len(result['errors'])} row(s) had errors — see below.",
                     "warning")
        else:
            flash("Please choose a CSV file.", "danger")
    return render_template("maintenance_config/scope_import.html",
                           result=result)
