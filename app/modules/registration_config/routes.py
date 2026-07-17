"""Registration Config blueprint: Registration Templates (PMS-style
scheduling for Vehicle Registration renewal). Mirrors
maintenance_config/routes.py's shape.
"""
from flask import (Blueprint, render_template, redirect, url_for, flash,
                   request, abort)
from flask_login import login_required

from app.core.security.decorators import require_permission
from app.core.security.registry import registry
from app.modules.registration_config.service import RegistrationTemplateService
from app.modules.master_data.reference.service import VehicleTypeService

bp = Blueprint("registration_config", __name__, url_prefix="/admin",
               template_folder="templates")

for _code, _desc in [
    ("registrationtemplate.view", "View Registration Templates"),
    ("registrationtemplate.create", "Create Registration Templates"),
    ("registrationtemplate.update", "Update Registration Templates"),
    ("registrationtemplate.delete", "Deactivate Registration Templates"),
]:
    _m, _a = _code.split(".")
    registry.register(_code, _m, _a, _desc)


def _items_from_form(f):
    codes = f.getlist("activity_code")
    descs = f.getlist("activity_description")
    return [{"activity_code": c, "activity_description": d, "sort_order": i + 1}
           for i, (c, d) in enumerate(zip(codes, descs)) if c and d]


@bp.route("/registration-templates")
@login_required
@require_permission("registrationtemplate.view")
def registrationtemplate_list():
    items = RegistrationTemplateService().list(include_inactive=True)
    return render_template("registration_config/template_list.html", items=items)


@bp.route("/registration-templates/new", methods=["GET", "POST"])
@login_required
@require_permission("registrationtemplate.create")
def registrationtemplate_new():
    from app.modules.master_data.vehicle_brand.service import VehicleBrandService
    vehicle_types = VehicleTypeService().list()
    vehicle_brands = VehicleBrandService().list()
    if request.method == "POST":
        f = request.form
        RegistrationTemplateService().create(
            vehicle_type_id=int(f["vehicle_type_id"]) if f.get("vehicle_type_id") else None,
            vehicle_brand_id=int(f["vehicle_brand_id"]) if f.get("vehicle_brand_id") else None,
            vehicle_model_id=int(f["vehicle_model_id"]) if f.get("vehicle_model_id") else None,
            interval_years=int(f.get("interval_years") or 3),
            next_generation_policy=f.get("next_generation_policy", "AUTO_SCHEDULE"),
            notify_before_days=int(f["notify_before_days"]) if f.get("notify_before_days") else None,
            priority=f.get("priority", "MEDIUM"),
            items=_items_from_form(f))
        flash("Registration Template created.", "success")
        return redirect(url_for("registration_config.registrationtemplate_list"))
    return render_template("registration_config/template_form.html",
                           item=None, vehicle_types=vehicle_types,
                           vehicle_brands=vehicle_brands, title="New Registration Template")


@bp.route("/registration-templates/<int:tid>")
@login_required
@require_permission("registrationtemplate.view")
def registrationtemplate_detail(tid):
    item = RegistrationTemplateService().get_by_id(tid)
    if item is None:
        abort(404)
    return render_template("registration_config/template_detail.html", item=item)


@bp.route("/registration-templates/<int:tid>/edit", methods=["GET", "POST"])
@login_required
@require_permission("registrationtemplate.update")
def registrationtemplate_edit(tid):
    from app.modules.master_data.vehicle_brand.service import VehicleBrandService
    item = RegistrationTemplateService().get_by_id(tid)
    if item is None:
        abort(404)
    vehicle_types = VehicleTypeService().list()
    vehicle_brands = VehicleBrandService().list()
    if request.method == "POST":
        f = request.form
        RegistrationTemplateService().update(
            tid,
            vehicle_type_id=int(f["vehicle_type_id"]) if f.get("vehicle_type_id") else None,
            vehicle_brand_id=int(f["vehicle_brand_id"]) if f.get("vehicle_brand_id") else None,
            vehicle_model_id=int(f["vehicle_model_id"]) if f.get("vehicle_model_id") else None,
            interval_years=int(f.get("interval_years") or 3),
            next_generation_policy=f.get("next_generation_policy", "AUTO_SCHEDULE"),
            notify_before_days=int(f["notify_before_days"]) if f.get("notify_before_days") else None,
            priority=f.get("priority", "MEDIUM"),
            items=_items_from_form(f))
        flash("Registration Template updated.", "success")
        return redirect(url_for("registration_config.registrationtemplate_detail", tid=tid))
    return render_template("registration_config/template_form.html",
                           item=item, vehicle_types=vehicle_types,
                           vehicle_brands=vehicle_brands,
                           title="Edit Registration Template")


@bp.route("/registration-templates/<int:tid>/deactivate", methods=["POST"])
@login_required
@require_permission("registrationtemplate.delete")
def registrationtemplate_deactivate(tid):
    RegistrationTemplateService().deactivate(tid)
    flash("Registration Template deactivated.", "info")
    return redirect(url_for("registration_config.registrationtemplate_list"))
