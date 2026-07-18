"""PM Configuration blueprint: PM Schedules, PM Scope Templates.
Thin controllers — business logic in services."""
from flask import (Blueprint, render_template, redirect, url_for, flash,
                   request, abort)
from flask_login import login_required

from app.core.security.decorators import require_permission
from app.core.security.registry import registry
from app.modules.maintenance_config.service import (
    PMScheduleService, PMScopeTemplateService, PMSProfileService,
    InvalidScheduleError, InvalidScopeError)
from app.modules.transactions.maintenance_order.service import (
    TransactionTypeService)
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
    ("pmprofile.view", "View PMS Profiles"),
    ("motransactiontype.view", "View MO Transaction Types"),
    ("motransactiontype.create", "Create MO Transaction Types"),
    ("motransactiontype.delete", "Deactivate MO Transaction Types"),
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


# ── PMS Profiles (PMS-2: grouped view of packages sharing a profile_code) ──

@bp.route("/pms-profiles")
@login_required
@require_permission("pmprofile.view")
def pmsprofile_list():
    profiles = PMSProfileService().list_profiles()
    return render_template("maintenance_config/profile_list.html",
                           profiles=profiles)


@bp.route("/pms-profiles/<profile_code>")
@login_required
@require_permission("pmprofile.view")
def pmsprofile_detail(profile_code):
    packages = PMSProfileService().get_profile(profile_code)
    if not packages:
        flash("No PMS Profile found with that code.", "warning")
        return redirect(url_for("maintenance_config.pmsprofile_list"))
    return render_template("maintenance_config/profile_detail.html",
                           profile_code=profile_code, packages=packages)


def _pmschedule_fields(f):
    from app.core.validation.date_utils import parse_form_date
    return dict(
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
        next_pms_generation=f.get("next_pms_generation", "AUTO_SCHEDULE"),
        next_due_calculation_method=f.get(
            "next_due_calculation_method", "ACTUAL_COMPLETION"),
        maintenance_type_id=int(f["maintenance_type_id"]),
        trigger_mode=f["trigger_mode"],
        interval_km=int(f["interval_km"]) if f.get("interval_km") else None,
        interval_days=int(f["interval_days"]) if f.get("interval_days") else None,
        priority=f.get("priority", "MEDIUM"),
        notify_before_km=int(f["notify_before_km"]) if f.get("notify_before_km") else None,
        notify_before_days=int(f["notify_before_days"]) if f.get("notify_before_days") else None,
        escalate_if_overdue=f.get("escalate_if_overdue") == "on")


def _pmschedule_form_context(item=None):
    from app.modules.system_admin.services.lookup_service import LookupService
    from app.modules.master_data.vehicle_brand.service import VehicleBrandService
    return dict(
        vehicle_types=VehicleTypeService().list(),
        maintenance_types=MaintenanceTypeService().list(),
        priorities=LookupService().get_by_type_with_fallback("PM_PRIORITY"),
        fuel_types=LookupService().get_by_type_with_fallback("FUEL_TYPE"),
        vehicle_brands=VehicleBrandService().list(),
        item=item)


@bp.route("/pm-schedules/new", methods=["GET", "POST"])
@login_required
@require_permission("pmschedule.create")
def pmschedule_new():
    from app.core.validation.date_utils import DateFormatError, RequiredFieldError
    ctx = _pmschedule_form_context()
    if request.method == "POST":
        try:
            PMScheduleService().create(**_pmschedule_fields(request.form))
            flash("PM Template created.", "success")
            return redirect(url_for("maintenance_config.pmschedule_list"))
        except (InvalidScheduleError, DateFormatError, RequiredFieldError) as e:
            flash(str(e), "danger")
    return render_template("maintenance_config/schedule_form.html",
                           title="New PM Template", **ctx)


@bp.route("/pm-schedules/<int:sid>")
@login_required
@require_permission("pmschedule.view")
def pmschedule_detail(sid):
    item = PMScheduleService().get_by_id(sid)
    if item is None:
        abort(404)
    return render_template("maintenance_config/schedule_detail.html", item=item)


@bp.route("/pm-schedules/<int:sid>/edit", methods=["GET", "POST"])
@login_required
@require_permission("pmschedule.update")
def pmschedule_edit(sid):
    from app.core.validation.date_utils import DateFormatError, RequiredFieldError
    item = PMScheduleService().get_by_id(sid)
    if item is None:
        abort(404)
    if request.method == "POST":
        try:
            PMScheduleService().update(sid, **_pmschedule_fields(request.form))
            flash("PM Template updated.", "success")
            return redirect(url_for("maintenance_config.pmschedule_detail", sid=sid))
        except (InvalidScheduleError, DateFormatError, RequiredFieldError) as e:
            flash(str(e), "danger")
    ctx = _pmschedule_form_context(item=item)
    return render_template("maintenance_config/schedule_form.html",
                           title=f"Edit PM Template — {item.maintenance_type.name}",
                           **ctx)


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


def _pmscope_items_from_form(f):
    codes = f.getlist("activity_code")
    descs = f.getlist("activity_description")
    return [{"activity_code": c, "activity_description": d, "sort_order": i + 1}
           for i, (c, d) in enumerate(zip(codes, descs)) if c and d]


@bp.route("/pm-scope-templates/new", methods=["GET", "POST"])
@login_required
@require_permission("pmscopetemplate.create")
def pmscope_new():
    maintenance_types = MaintenanceTypeService().list()
    pm_schedules = PMScheduleService().list()
    if request.method == "POST":
        f = request.form
        items = _pmscope_items_from_form(f)
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
                           item=None,
                           title="New PM Scope Template")


@bp.route("/pm-scope-templates/<int:tid>")
@login_required
@require_permission("pmscopetemplate.view")
def pmscope_detail(tid):
    item = PMScopeTemplateService().get_by_id(tid)
    if item is None:
        abort(404)
    return render_template("maintenance_config/scope_detail.html", item=item)


@bp.route("/pm-scope-templates/<int:tid>/edit", methods=["GET", "POST"])
@login_required
@require_permission("pmscopetemplate.update")
def pmscope_edit(tid):
    item = PMScopeTemplateService().get_by_id(tid)
    if item is None:
        abort(404)
    maintenance_types = MaintenanceTypeService().list()
    pm_schedules = PMScheduleService().list()
    if request.method == "POST":
        f = request.form
        items = _pmscope_items_from_form(f)
        try:
            PMScopeTemplateService().update(
                tid, name=f.get("name"), description=f.get("description"),
                pm_schedule_id=int(f["pm_schedule_id"]) if f.get("pm_schedule_id") else None,
                items=items)
            flash("PM Scope Template updated.", "success")
            return redirect(url_for("maintenance_config.pmscope_detail", tid=tid))
        except InvalidScopeError as e:
            flash(str(e), "danger")
    return render_template("maintenance_config/scope_form.html",
                           maintenance_types=maintenance_types,
                           pm_schedules=pm_schedules,
                           item=item,
                           title=f"Edit — {item.name}")


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


# ── MO Transaction Types ─────────────────────────────────────────────────

@bp.route("/mo-transaction-types")
@login_required
@require_permission("motransactiontype.view")
def mo_transaction_type_list():
    items = TransactionTypeService().list(include_inactive=True)
    return render_template("maintenance_config/mo_transaction_type_list.html",
                           items=items)


@bp.route("/mo-transaction-types/new", methods=["GET", "POST"])
@login_required
@require_permission("motransactiontype.create")
def mo_transaction_type_new():
    if request.method == "POST":
        f = request.form
        TransactionTypeService().create(
            code=f["code"], name=f["name"], order_category=f["order_category"],
            group=f.get("group") or None,
            sort_order=int(f.get("sort_order") or 0))
        flash("Transaction Type created.", "success")
        return redirect(url_for("maintenance_config.mo_transaction_type_list"))
    return render_template("maintenance_config/mo_transaction_type_form.html",
                           title="New MO Transaction Type")


@bp.route("/mo-transaction-types/<int:tt_id>/deactivate", methods=["POST"])
@login_required
@require_permission("motransactiontype.delete")
def mo_transaction_type_deactivate(tt_id):
    TransactionTypeService().deactivate(tt_id)
    flash("Transaction Type deactivated.", "info")
    return redirect(url_for("maintenance_config.mo_transaction_type_list"))


@bp.route("/mo-transaction-types/<int:tt_id>/reactivate", methods=["POST"])
@login_required
@require_permission("motransactiontype.create")
def mo_transaction_type_reactivate(tt_id):
    TransactionTypeService().reactivate(tt_id)
    flash("Transaction Type reactivated.", "success")
    return redirect(url_for("maintenance_config.mo_transaction_type_list"))
