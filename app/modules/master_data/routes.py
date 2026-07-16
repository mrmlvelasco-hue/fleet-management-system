"""Master Data blueprint — all 10 modules (org, reference, asset masters).
Thin controllers: parse → service → render. All business logic in services."""
from datetime import date

from flask import (Blueprint, render_template, redirect, url_for,
                   flash, request, jsonify, send_from_directory, current_app,
                   abort)
from flask_login import login_required, current_user

from app.core.security.decorators import require_permission
from app.core.security.registry import registry
from app.core.attachments.service import AttachmentService, AttachmentError
from app.core.attachments.models import Attachment
from app.core.validation.date_utils import (
    parse_form_date, DateFormatError, RequiredFieldError)
from app.modules.system_admin.services.lookup_service import (
    LookupService, registry as lookup_registry)
from app.modules.master_data.org.models import Branch, Department, BusinessUnit
from app.modules.master_data.org.service import (
    BranchService, DepartmentService, BusinessUnitService, DuplicateCodeError)
from app.modules.master_data.reference.models import VehicleType, MaintenanceType
from app.modules.master_data.reference.service import (
    VehicleTypeService, MaintenanceTypeService)
from app.modules.master_data.vehicle.models import Vehicle
from app.modules.master_data.vehicle.service import (
    VehicleService, DuplicateVehicleError, InvalidVehicleDataError,
    BrandRequiredError, ModelRequiredError, InvalidBrandError,
    InvalidModelError, ModelBrandMismatchError)
from app.modules.master_data.vehicle_brand.models import (
    VehicleBrand, VehicleModel)
from app.modules.master_data.vehicle_brand.service import (
    VehicleBrandService, VehicleModelService,
    DuplicateBrandError, DuplicateModelError)
from app.modules.master_data.driver.models import Driver
from app.modules.master_data.driver.service import (
    DriverService, DuplicateDriverError)
from app.modules.master_data.tire.models import Tire
from app.modules.master_data.tire.service import TireService, DuplicateSerialError
from app.modules.master_data.battery.models import Battery
from app.modules.master_data.battery.service import BatteryService
from app.modules.master_data.vendor.models import Vendor
from app.modules.master_data.vendor.service import VendorService
from app.extensions import db
import os

bp = Blueprint("master_data", __name__, url_prefix="/master",
               template_folder="templates")

# ── Register permissions ───────────────────────────────────────────────────
for _mod in ["vehicle", "driver", "tire", "battery", "vendor",
             "branch", "department", "businessunit",
             "vehicletype", "maintenancetype",
             "vehiclebrand", "vehiclemodel"]:
    for _act in ["view", "create", "update", "delete"]:
        _code = f"{_mod}.{_act}"
        registry.register(_code, _mod, _act, f"{_act.title()} {_mod}")
for _act in ["upload", "delete"]:
    registry.register(f"attachment.{_act}", "attachment", _act,
                      f"{_act.title()} attachments")

# Lookup-driven dropdowns for Vehicle/Driver masters (idempotent seed via
# `flask seed all` -> sync_lookups()).
for _code, _desc, _order in [
    ("DIESEL", "Diesel", 1), ("GASOLINE", "Gasoline", 2),
    ("ELECTRIC", "Electric", 3), ("HYBRID", "Hybrid", 4), ("LPG", "LPG Gas", 5),
]:
    lookup_registry.register("FUEL_TYPE", _code, _desc, _order)

for _code, _desc, _order in [
    ("SEDAN", "Sedan", 1), ("SUV", "SUV", 2), ("VAN", "Van", 3),
    ("PICKUP", "Pickup", 4), ("TRUCK", "Truck", 5), ("MOTORCYCLE", "Motorcycle", 6),
    ("BUS", "Bus", 7), ("OTHER", "Other", 8),
]:
    lookup_registry.register("VEHICLE_BODY_TYPE", _code, _desc, _order)

for _code, _desc, _order in [
    ("LIGHT_FLEET", "Light Fleet", 1), ("HEAVY_FLEET", "Heavy Fleet", 2),
    ("EXECUTIVE", "Executive", 3), ("POOL", "Pool", 4), ("OTHER", "Other", 5),
]:
    lookup_registry.register("COMPONENT_GROUP", _code, _desc, _order)

for _code, _desc, _order in [
    ("PROFESSIONAL", "Professional", 1),
    ("NON_PROFESSIONAL", "Non-Professional", 2),
    ("STUDENT_PERMIT", "Student Permit", 3),
]:
    lookup_registry.register("LICENSE_TYPE", _code, _desc, _order)

for _code, _desc, _order in [
    ("LIGHT", "Light Vehicle", 1), ("HEAVY", "Heavy Vehicle", 2),
    ("MOTORCYCLE", "Motorcycle", 3), ("SPECIAL", "Special Purpose", 4),
]:
    lookup_registry.register("VEHICLE_CATEGORY", _code, _desc, _order)

for _code, _desc, _order in [
    ("GOODS", "Goods", 1), ("SERVICES", "Services", 2), ("BOTH", "Both", 3),
]:
    lookup_registry.register("VENDOR_TYPE", _code, _desc, _order)

for _code, _desc, _order in [
    ("RADIAL", "Radial", 1), ("BIAS", "Bias", 2),
]:
    lookup_registry.register("TIRE_TYPE", _code, _desc, _order)

for _code, _desc, _order in [
    ("TRANSFER", "Transfer", 1), ("DISPATCH", "Dispatch", 2),
    ("RETURN", "Return", 3), ("OTHER", "Other", 4),
]:
    lookup_registry.register("MOVEMENT_TYPE", _code, _desc, _order)

for _code, _desc, _order in [
    ("LOW", "Low", 1), ("MEDIUM", "Medium", 2), ("HIGH", "High", 3),
]:
    lookup_registry.register("PM_PRIORITY", _code, _desc, _order)


# ── Helpers ────────────────────────────────────────────────────────────────

def _flash_confirm_script():
    return """<script>
document.querySelectorAll("form.fms-confirm").forEach(function(f){
  f.addEventListener("submit",function(e){e.preventDefault();
  Swal.fire({title:f.dataset.confirm,icon:"warning",showCancelButton:true,
  confirmButtonText:"Yes"}).then(function(r){if(r.isConfirmed)f.submit();});});});
</script>"""


def _attachment_rows(table, ref_id):
    return AttachmentService().list_for(table, ref_id)


# ── Branches ───────────────────────────────────────────────────────────────

@bp.route("/branches")
@login_required
@require_permission("branch.view")
def branch_list():
    items = BranchService().list(include_inactive=True)
    return render_template("master_data/branch_list.html", items=items)


@bp.route("/branches/new", methods=["GET", "POST"])
@login_required
@require_permission("branch.create")
def branch_new():
    if request.method == "POST":
        try:
            BranchService().create(**_branch_fields())
            flash("Branch created.", "success")
            return redirect(url_for("master_data.branch_list"))
        except DuplicateCodeError as e:
            flash(str(e), "danger")
    return render_template("master_data/branch_form.html",
                           item=None, title="New Branch")


@bp.route("/branches/<int:bid>/edit", methods=["GET", "POST"])
@login_required
@require_permission("branch.update")
def branch_edit(bid):
    item = db.session.get(Branch, bid)
    if request.method == "POST":
        BranchService().update(bid, **_branch_fields())
        flash("Branch updated.", "success")
        return redirect(url_for("master_data.branch_list"))
    return render_template("master_data/branch_form.html",
                           item=item, title=f"Edit Branch — {item.code}")


@bp.route("/branches/<int:bid>/deactivate", methods=["POST"])
@login_required
@require_permission("branch.delete")
def branch_deactivate(bid):
    BranchService().deactivate(bid)
    flash("Branch deactivated.", "info")
    return redirect(url_for("master_data.branch_list"))


def _branch_fields():
    return {k: request.form.get(k, "")
            for k in ["code", "name", "address", "city", "phone", "email"]}


# ── Departments ────────────────────────────────────────────────────────────

@bp.route("/departments")
@login_required
@require_permission("department.view")
def department_list():
    items = DepartmentService().list(include_inactive=True)
    branches = BranchService().list()
    return render_template("master_data/department_list.html",
                           items=items, branches=branches)


@bp.route("/departments/new", methods=["GET", "POST"])
@login_required
@require_permission("department.create")
def department_new():
    if request.method == "POST":
        try:
            DepartmentService().create(
                code=request.form["code"], name=request.form["name"],
                branch_id=int(request.form["branch_id"]),
                description=request.form.get("description", ""))
            flash("Department created.", "success")
            return redirect(url_for("master_data.department_list"))
        except DuplicateCodeError as e:
            flash(str(e), "danger")
    return render_template("master_data/department_form.html",
                           item=None, title="New Department")


@bp.route("/departments/<int:did>/edit", methods=["GET", "POST"])
@login_required
@require_permission("department.update")
def department_edit(did):
    item = db.session.get(Department, did)
    if request.method == "POST":
        DepartmentService().update(
            did, name=request.form["name"],
            description=request.form.get("description", ""))
        flash("Department updated.", "success")
        return redirect(url_for("master_data.department_list"))
    return render_template("master_data/department_form.html",
                           item=item,
                           title=f"Edit Department — {item.code}")


@bp.route("/departments/<int:did>/deactivate", methods=["POST"])
@login_required
@require_permission("department.delete")
def department_deactivate(did):
    DepartmentService().deactivate(did)
    flash("Department deactivated.", "info")
    return redirect(url_for("master_data.department_list"))


# ── Business Units ─────────────────────────────────────────────────────────

@bp.route("/business-units")
@login_required
@require_permission("businessunit.view")
def businessunit_list():
    items = BusinessUnitService().list(include_inactive=True)
    return render_template("master_data/businessunit_list.html", items=items)


@bp.route("/business-units/new", methods=["GET", "POST"])
@login_required
@require_permission("businessunit.create")
def businessunit_new():
    if request.method == "POST":
        try:
            BusinessUnitService().create(
                code=request.form["code"], name=request.form["name"],
                description=request.form.get("description", ""))
            flash("Business unit created.", "success")
            return redirect(url_for("master_data.businessunit_list"))
        except DuplicateCodeError as e:
            flash(str(e), "danger")
    return render_template("master_data/businessunit_form.html",
                           item=None, title="New Business Unit")


@bp.route("/business-units/<int:bid>/edit", methods=["GET", "POST"])
@login_required
@require_permission("businessunit.update")
def businessunit_edit(bid):
    item = db.session.get(BusinessUnit, bid)
    if request.method == "POST":
        BusinessUnitService().update(
            bid, name=request.form["name"],
            description=request.form.get("description", ""))
        flash("Business unit updated.", "success")
        return redirect(url_for("master_data.businessunit_list"))
    return render_template("master_data/businessunit_form.html",
                           item=item, title=f"Edit — {item.code}")


@bp.route("/business-units/<int:bid>/deactivate", methods=["POST"])
@login_required
@require_permission("businessunit.delete")
def businessunit_deactivate(bid):
    BusinessUnitService().deactivate(bid)
    flash("Business unit deactivated.", "info")
    return redirect(url_for("master_data.businessunit_list"))


# ── Vehicle Types ──────────────────────────────────────────────────────────

@bp.route("/vehicle-types")
@login_required
@require_permission("vehicletype.view")
def vehicletype_list():
    items = VehicleTypeService().list(include_inactive=True)
    return render_template("master_data/vehicletype_list.html", items=items)


@bp.route("/vehicle-types/new", methods=["GET", "POST"])
@login_required
@require_permission("vehicletype.create")
def vehicletype_new():
    from app.modules.system_admin.services.lookup_service import LookupService
    categories = LookupService().get_by_type_with_fallback("VEHICLE_CATEGORY")
    if request.method == "POST":
        try:
            VehicleTypeService().create(
                code=request.form["code"], name=request.form["name"],
                category=request.form["category"],
                description=request.form.get("description", ""))
            flash("Vehicle type created.", "success")
            return redirect(url_for("master_data.vehicletype_list"))
        except DuplicateCodeError as e:
            flash(str(e), "danger")
    return render_template("master_data/vehicletype_form.html",
                           item=None, categories=categories,
                           title="New Vehicle Type")


@bp.route("/vehicle-types/<int:vid>/edit", methods=["GET", "POST"])
@login_required
@require_permission("vehicletype.update")
def vehicletype_edit(vid):
    from app.modules.system_admin.services.lookup_service import LookupService
    categories = LookupService().get_by_type_with_fallback("VEHICLE_CATEGORY")
    item = db.session.get(VehicleType, vid)
    if request.method == "POST":
        VehicleTypeService().update(
            vid, name=request.form["name"],
            category=request.form["category"],
            description=request.form.get("description", ""))
        flash("Vehicle type updated.", "success")
        return redirect(url_for("master_data.vehicletype_list"))
    return render_template("master_data/vehicletype_form.html",
                           item=item, categories=categories,
                           title=f"Edit — {item.code}")


@bp.route("/vehicle-types/<int:vid>/deactivate", methods=["POST"])
@login_required
@require_permission("vehicletype.delete")
def vehicletype_deactivate(vid):
    VehicleTypeService().deactivate(vid)
    flash("Vehicle type deactivated.", "info")
    return redirect(url_for("master_data.vehicletype_list"))


# ── Maintenance Types ──────────────────────────────────────────────────────

@bp.route("/maintenance-types")
@login_required
@require_permission("maintenancetype.view")
def maintenancetype_list():
    items = MaintenanceTypeService().list(include_inactive=True)
    return render_template("master_data/maintenancetype_list.html",
                           items=items)


@bp.route("/maintenance-types/new", methods=["GET", "POST"])
@login_required
@require_permission("maintenancetype.create")
def maintenancetype_new():
    if request.method == "POST":
        try:
            MaintenanceTypeService().create(
                code=request.form["code"], name=request.form["name"],
                category=request.form["category"],
                description=request.form.get("description", ""))
            flash("Maintenance type created.", "success")
            return redirect(url_for("master_data.maintenancetype_list"))
        except DuplicateCodeError as e:
            flash(str(e), "danger")
    return render_template("master_data/maintenancetype_form.html",
                           item=None, title="New Maintenance Type")


@bp.route("/maintenance-types/<int:mid>/edit", methods=["GET", "POST"])
@login_required
@require_permission("maintenancetype.update")
def maintenancetype_edit(mid):
    item = db.session.get(MaintenanceType, mid)
    if request.method == "POST":
        MaintenanceTypeService().update(
            mid, name=request.form["name"],
            category=request.form["category"],
            description=request.form.get("description", ""))
        flash("Maintenance type updated.", "success")
        return redirect(url_for("master_data.maintenancetype_list"))
    return render_template("master_data/maintenancetype_form.html",
                           item=item, title=f"Edit — {item.code}")


@bp.route("/maintenance-types/<int:mid>/deactivate", methods=["POST"])
@login_required
@require_permission("maintenancetype.delete")
def maintenancetype_deactivate(mid):
    MaintenanceTypeService().deactivate(mid)
    flash("Maintenance type deactivated.", "info")
    return redirect(url_for("master_data.maintenancetype_list"))


# ── Vendors ────────────────────────────────────────────────────────────────

@bp.route("/vendors")
@login_required
@require_permission("vendor.view")
def vendor_list():
    items = VendorService().list(include_inactive=True, user=current_user)
    return render_template("master_data/vendor_list.html", items=items)


@bp.route("/vendors/new", methods=["GET", "POST"])
@login_required
@require_permission("vendor.create")
def vendor_new():
    from app.modules.system_admin.services.lookup_service import LookupService
    vendor_types = LookupService().get_by_type_with_fallback("VENDOR_TYPE")
    if request.method == "POST":
        try:
            vendor = VendorService().create(**_vendor_fields())
            branch_ids = [int(b) for b in request.form.getlist("branch_ids")]
            if branch_ids:
                VendorService().assign_branches(vendor.id, branch_ids)
            flash("Vendor created.", "success")
            return redirect(url_for("master_data.vendor_list"))
        except Exception as e:
            flash(str(e), "danger")
    return render_template("master_data/vendor_form.html",
                           item=None, vendor_types=vendor_types,
                           title="New Vendor")


@bp.route("/vendors/<int:vid>/edit", methods=["GET", "POST"])
@login_required
@require_permission("vendor.update")
def vendor_edit(vid):
    from app.modules.system_admin.services.lookup_service import LookupService
    vendor_types = LookupService().get_by_type_with_fallback("VENDOR_TYPE")
    item = VendorService().get_visible(vid, current_user)
    if item is None:
        abort(403)
    if request.method == "POST":
        VendorService().update(vid, **_vendor_fields(include_code=False))
        branch_ids = [int(b) for b in request.form.getlist("branch_ids")]
        VendorService().assign_branches(vid, branch_ids)
        flash("Vendor updated.", "success")
        return redirect(url_for("master_data.vendor_list"))
    return render_template("master_data/vendor_form.html",
                           item=item, vendor_types=vendor_types,
                           title=f"Edit — {item.code}")


@bp.route("/vendors/<int:vid>/deactivate", methods=["POST"])
@login_required
@require_permission("vendor.delete")
def vendor_deactivate(vid):
    VendorService().deactivate(vid)
    flash("Vendor deactivated.", "info")
    return redirect(url_for("master_data.vendor_list"))


def _vendor_fields(include_code=True):
    fields = ["name", "address", "city", "phone", "email",
              "tin", "contact_person", "vendor_type"]
    if include_code:
        fields = ["code"] + fields
    return {k: request.form.get(k, "") for k in fields}


# ── Vehicle Brands ───────────────────────────────────────────────────────────

@bp.route("/vehicle-brands")
@login_required
@require_permission("vehiclebrand.view")
def vehiclebrand_list():
    items = VehicleBrandService().list(include_inactive=True)
    return render_template("master_data/vehiclebrand_list.html", items=items)


@bp.route("/vehicle-brands/new", methods=["GET", "POST"])
@login_required
@require_permission("vehiclebrand.create")
def vehiclebrand_new():
    if request.method == "POST":
        try:
            VehicleBrandService().create(name=request.form["name"])
            flash("Vehicle brand created.", "success")
            return redirect(url_for("master_data.vehiclebrand_list"))
        except DuplicateBrandError as e:
            flash(str(e), "danger")
    return render_template("master_data/vehiclebrand_form.html",
                           item=None, title="New Vehicle Brand")


@bp.route("/vehicle-brands/<int:bid>/edit", methods=["GET", "POST"])
@login_required
@require_permission("vehiclebrand.update")
def vehiclebrand_edit(bid):
    item = db.session.get(VehicleBrand, bid)
    if item is None:
        flash("Vehicle brand not found.", "warning")
        return redirect(url_for("master_data.vehiclebrand_list"))
    if request.method == "POST":
        try:
            VehicleBrandService().update(bid, name=request.form["name"])
            flash("Vehicle brand updated.", "success")
            return redirect(url_for("master_data.vehiclebrand_list"))
        except DuplicateBrandError as e:
            flash(str(e), "danger")
    return render_template("master_data/vehiclebrand_form.html",
                           item=item, title=f"Edit — {item.name}")


@bp.route("/vehicle-brands/<int:bid>/deactivate", methods=["POST"])
@login_required
@require_permission("vehiclebrand.delete")
def vehiclebrand_deactivate(bid):
    VehicleBrandService().deactivate(bid)
    flash("Vehicle brand deactivated.", "info")
    return redirect(url_for("master_data.vehiclebrand_list"))


# ── Vehicle Models ───────────────────────────────────────────────────────────

@bp.route("/vehicle-models")
@login_required
@require_permission("vehiclemodel.view")
def vehiclemodel_list():
    items = VehicleModelService().list(include_inactive=True)
    return render_template("master_data/vehiclemodel_list.html", items=items)


@bp.route("/vehicle-models/new", methods=["GET", "POST"])
@login_required
@require_permission("vehiclemodel.create")
def vehiclemodel_new():
    brands = VehicleBrandService().list()
    if request.method == "POST":
        try:
            VehicleModelService().create(
                brand_id=int(request.form["brand_id"]),
                name=request.form["name"])
            flash("Vehicle model created.", "success")
            return redirect(url_for("master_data.vehiclemodel_list"))
        except DuplicateModelError as e:
            flash(str(e), "danger")
    return render_template("master_data/vehiclemodel_form.html",
                           item=None, brands=brands, title="New Vehicle Model")


@bp.route("/vehicle-models/<int:mid>/edit", methods=["GET", "POST"])
@login_required
@require_permission("vehiclemodel.update")
def vehiclemodel_edit(mid):
    item = db.session.get(VehicleModel, mid)
    brands = VehicleBrandService().list()
    if item is None:
        flash("Vehicle model not found.", "warning")
        return redirect(url_for("master_data.vehiclemodel_list"))
    if request.method == "POST":
        try:
            VehicleModelService().update(mid, name=request.form["name"])
            flash("Vehicle model updated.", "success")
            return redirect(url_for("master_data.vehiclemodel_list"))
        except DuplicateModelError as e:
            flash(str(e), "danger")
    return render_template("master_data/vehiclemodel_form.html",
                           item=item, brands=brands,
                           title=f"Edit — {item.name}")


@bp.route("/vehicle-models/<int:mid>/deactivate", methods=["POST"])
@login_required
@require_permission("vehiclemodel.delete")
def vehiclemodel_deactivate(mid):
    VehicleModelService().deactivate(mid)
    flash("Vehicle model deactivated.", "info")
    return redirect(url_for("master_data.vehiclemodel_list"))


# ── Vehicles ───────────────────────────────────────────────────────────────

@bp.route("/vehicles")
@login_required
@require_permission("vehicle.view")
def vehicle_list():
    items = VehicleService().list(include_inactive=True, user=current_user)
    return render_template("master_data/vehicle_list.html", items=items)


@bp.route("/vehicles/<int:vid>")
@login_required
@require_permission("vehicle.view")
def vehicle_detail(vid):
    item = VehicleService().get_visible(vid, current_user)
    if item is None:
        abort(403)
    attachments = _attachment_rows("vehicles", vid)
    maintenance_history = []
    registration_history = []
    try:
        from app.modules.transactions.maintenance_order.models import (
            MaintenanceOrder)
        maintenance_history = (MaintenanceOrder.query
                              .filter_by(vehicle_id=vid, status="COMPLETED")
                              .order_by(MaintenanceOrder.completed_date.desc())
                              .all())
    except Exception:
        pass  # transactions module may not be loaded in older phases
    try:
        from app.modules.transactions.vehicle_registration.models import (
            VehicleRegistration)
        registration_history = (VehicleRegistration.query
                               .filter_by(vehicle_id=vid)
                               .order_by(VehicleRegistration.id.desc())
                               .all())
    except Exception:
        pass
    return render_template("master_data/vehicle_detail.html",
                           item=item, vehicle=item, attachments=attachments,
                           maintenance_history=maintenance_history,
                           registration_history=registration_history)


@bp.route("/vehicles/<int:vid>/clone")
@login_required
@require_permission("vehicle.create")
def vehicle_clone(vid):
    """Pre-fills the New Vehicle form with an existing vehicle's data —
    unique identifiers (plate/conduction/chassis/engine numbers) are
    deliberately left blank so the clone can't collide with the original."""
    from app.modules.maintenance_config.service import PMScheduleService
    from app.modules.master_data.vehicle_brand.service import VehicleBrandService
    from app.core.validation.form_echo import FormEcho
    clone_data = VehicleService().get_clone_data(vid)
    if not clone_data:
        abort(404)
    branch = (db.session.get(Branch, clone_data["branch_id"])
             if clone_data.get("branch_id") else None)
    item = FormEcho(clone_data, branch=branch)
    flash("Reviewing a clone of this vehicle — unique fields (Plate, "
         "Conduction, Chassis, Engine numbers) have been cleared; fill "
         "in new ones before saving.", "info")
    return render_template(
        "master_data/vehicle_form.html", item=item,
        vtypes=VehicleTypeService().list(), vehicle_types=VehicleTypeService().list(),
        departments=DepartmentService().list(), bus=BusinessUnitService().list(),
        fuel_types=LookupService().get_by_type("FUEL_TYPE"),
        vehicle_body_types=LookupService().get_by_type_with_fallback("VEHICLE_BODY_TYPE"),
        component_groups=LookupService().get_by_type_with_fallback("COMPONENT_GROUP"),
        pm_schedules=PMScheduleService().list(),
        vehicle_brands=VehicleBrandService().list(),
        brand_ids_by_name={b.name: b.id for b in VehicleBrandService().list()},
        error_field=None, title="New Vehicle (Cloned)")


@bp.route("/vehicles/new", methods=["GET", "POST"])
@login_required
@require_permission("vehicle.create")
def vehicle_new():
    from app.modules.maintenance_config.service import PMScheduleService
    from app.modules.master_data.vehicle_brand.service import VehicleBrandService
    from app.core.validation.form_echo import FormEcho
    vtypes = VehicleTypeService().list()
    departments = DepartmentService().list()
    bus = BusinessUnitService().list()
    fuel_types = LookupService().get_by_type("FUEL_TYPE")
    vehicle_body_types = LookupService().get_by_type_with_fallback("VEHICLE_BODY_TYPE")
    component_groups = LookupService().get_by_type_with_fallback("COMPONENT_GROUP")
    pm_schedules = PMScheduleService().list()
    vehicle_brands = VehicleBrandService().list()
    brand_ids_by_name = {b.name: b.id for b in vehicle_brands}
    item = None
    error_field = None
    if request.method == "POST":
        try:
            VehicleService().create(**_vehicle_fields(), strict=True)
            flash("Vehicle created.", "success")
            return redirect(url_for("master_data.vehicle_list"))
        except (DuplicateVehicleError, InvalidVehicleDataError,
                DateFormatError, RequiredFieldError,
                BrandRequiredError, ModelRequiredError, InvalidBrandError,
                InvalidModelError, ModelBrandMismatchError) as e:
            flash(str(e), "danger")
            error_field = _guess_error_field(e)
            branch = None
            if request.form.get("branch_id"):
                branch = db.session.get(Branch, int(request.form["branch_id"]))
            item = FormEcho(request.form, branch=branch)
    return render_template("master_data/vehicle_form.html",
                           item=item, vtypes=vtypes, vehicle_types=vtypes,
                           departments=departments,
                           bus=bus, fuel_types=fuel_types,
                           vehicle_body_types=vehicle_body_types,
                           component_groups=component_groups,
                           pm_schedules=pm_schedules,
                           vehicle_brands=vehicle_brands,
                           brand_ids_by_name=brand_ids_by_name,
                           error_field=error_field,
                           title="New Vehicle")


@bp.route("/vehicles/<int:vid>/edit", methods=["GET", "POST"])
@login_required
@require_permission("vehicle.update")
def vehicle_edit(vid):
    from app.modules.maintenance_config.service import PMScheduleService
    from app.modules.master_data.vehicle_brand.service import VehicleBrandService
    from app.core.validation.form_echo import FormEcho
    item = db.session.get(Vehicle, vid)
    original_label = item.conduction_number or item.plate_number
    vtypes = VehicleTypeService().list()
    departments = DepartmentService().list()
    bus = BusinessUnitService().list()
    fuel_types = LookupService().get_by_type("FUEL_TYPE")
    vehicle_body_types = LookupService().get_by_type_with_fallback("VEHICLE_BODY_TYPE")
    component_groups = LookupService().get_by_type_with_fallback("COMPONENT_GROUP")
    pm_schedules = PMScheduleService().list()
    vehicle_brands = VehicleBrandService().list()
    brand_ids_by_name = {b.name: b.id for b in vehicle_brands}
    error_field = None
    if request.method == "POST":
        try:
            VehicleService().update(vid, **_vehicle_fields(include_conduction=False),
                                    strict=True)
            flash("Vehicle updated.", "success")
            return redirect(url_for("master_data.vehicle_detail", vid=vid))
        except (InvalidVehicleDataError, DateFormatError, RequiredFieldError,
                BrandRequiredError, ModelRequiredError, InvalidBrandError,
                InvalidModelError, ModelBrandMismatchError) as e:
            flash(str(e), "danger")
            error_field = _guess_error_field(e)
            # Show what the user just typed, not the stale saved values —
            # same fix as vehicle_new (preserve submitted data on error).
            branch = item.branch if item else None
            item = FormEcho(request.form, branch=branch)
    return render_template("master_data/vehicle_form.html",
                           item=item, vtypes=vtypes, vehicle_types=vtypes,
                           departments=departments,
                           bus=bus, fuel_types=fuel_types,
                           vehicle_body_types=vehicle_body_types,
                           component_groups=component_groups,
                           pm_schedules=pm_schedules,
                           vehicle_brands=vehicle_brands,
                           brand_ids_by_name=brand_ids_by_name,
                           error_field=error_field,
                           title=f"Edit — {original_label}")


@bp.route("/vehicles/<int:vid>/deactivate", methods=["POST"])
@login_required
@require_permission("vehicle.delete")
def vehicle_deactivate(vid):
    VehicleService().deactivate(vid)
    flash("Vehicle deactivated.", "info")
    return redirect(url_for("master_data.vehicle_list"))


_ERROR_FIELD_HINTS = [
    ("acquisition date", "acquisition_date"),
    ("plate number", "plate_number"),
    ("conduction number", "conduction_number"),
    ("brand", "brand"),
    ("model", "model"),
    ("license expiry", "license_expiry"),
    ("registration date", "registration_date"),
    ("valid from", "valid_from"),
    ("valid to", "valid_to"),
    ("purchase date", "acquisition_date"),
]


def _guess_error_field(exc: Exception):
    """Best-effort mapping from a validation error's message to the form
    field it concerns, so the template can highlight it. Returns None
    (no highlight) if the message doesn't match a known hint — the error
    is still shown via flash either way, this is purely a UX nicety."""
    message = str(exc).lower()
    for hint, field_name in _ERROR_FIELD_HINTS:
        if hint in message:
            return field_name
    return None


def _vehicle_fields(include_conduction=True):
    f = request.form
    d = dict(
        vehicle_type_id=int(f["vehicle_type_id"]),
        brand=f.get("brand", ""),
        model=f.get("model", ""),
        year=int(f.get("year", 2024)),
        color=f.get("color", ""),
        fuel_type=f.get("fuel_type", ""),
        variant=f.get("variant") or None,
        engine_type=f.get("engine_type") or None,
        transmission=f.get("transmission") or None,
        current_engine_hours=int(f["current_engine_hours"]) if f.get("current_engine_hours") else None,
        branch_id=int(f["branch_id"]),
        department_id=int(f["department_id"]) if f.get("department_id") else None,
        business_unit_id=int(f["business_unit_id"]) if f.get("business_unit_id") else None,
        chassis_number=f.get("chassis_number") or None,
        engine_number=f.get("engine_number") or None,
        plate_number=f.get("plate_number") or None,
        acquisition_date=parse_form_date(f.get("acquisition_date"),
                                         "Purchase Date"),
        acquisition_cost=f.get("acquisition_cost") or None,
        current_odometer=int(f.get("current_odometer") or 0),
        pm_schedule_id=int(f["pm_schedule_id"]) if f.get("pm_schedule_id") else None,
        assigned_driver_id=int(f["assigned_driver_id"]) if f.get("assigned_driver_id") else None,
        notes=f.get("notes", ""),
        # ── Vehicle Master enhancement ──
        far_number=f.get("far_number") or None,
        cr_number=f.get("cr_number") or None,
        mv_file_number=f.get("mv_file_number") or None,
        remarks=f.get("remarks") or None,
        vehicle_body_type=f.get("vehicle_body_type") or None,
        displacement=f.get("displacement") or None,
        component_group=f.get("component_group") or None,
        supplier=f.get("supplier") or None,
        leasing_company=f.get("leasing_company") or None,
        top_up_amount=f.get("top_up_amount") or None,
        assured_value_current_year=f.get("assured_value_current_year") or None,
        delivery_date=parse_form_date(f.get("delivery_date"), "Delivery Date"),
        start_date=parse_form_date(f.get("start_date"), "Start Date"),
        end_date=parse_form_date(f.get("end_date"), "End Date"),
        insurance_reference_number=f.get("insurance_reference_number") or None,
        comprehensive_policy_number=f.get("comprehensive_policy_number") or None,
        comprehensive_insurance_provider=f.get("comprehensive_insurance_provider") or None,
        ctpl_policy_number=f.get("ctpl_policy_number") or None,
        ctpl_insurance_provider=f.get("ctpl_insurance_provider") or None,
        lto_office=f.get("lto_office") or None,
        has_ctpl=f.get("has_ctpl") == "on",
        ctpl_from_date=parse_form_date(f.get("ctpl_from_date"), "CTPL From Date"),
        ctpl_to_date=parse_form_date(f.get("ctpl_to_date"), "CTPL To Date"),
        has_od_theft_aon=f.get("has_od_theft_aon") == "on",
        od_theft_aon_from_date=parse_form_date(f.get("od_theft_aon_from_date"), "OD/THEFT/AON From Date"),
        od_theft_aon_to_date=parse_form_date(f.get("od_theft_aon_to_date"), "OD/THEFT/AON To Date"),
        has_vtpl_pd=f.get("has_vtpl_pd") == "on",
        vtpl_pd_from_date=parse_form_date(f.get("vtpl_pd_from_date"), "VTPL/PD From Date"),
        vtpl_pd_to_date=parse_form_date(f.get("vtpl_pd_to_date"), "VTPL/PD To Date"),
        has_vtpl_bi=f.get("has_vtpl_bi") == "on",
        vtpl_bi_from_date=parse_form_date(f.get("vtpl_bi_from_date"), "VTPL/BI From Date"),
        vtpl_bi_to_date=parse_form_date(f.get("vtpl_bi_to_date"), "VTPL/BI To Date"),
        has_inland_marine=f.get("has_inland_marine") == "on",
        assignment=f.get("assignment") or None,
        assignment_group_classification=f.get("assignment_group_classification") or None,
        vehicle_usage=f.get("vehicle_usage") or None,
        mr_eds=(f.get("mr_eds") == "YES") if f.get("mr_eds") else None,
        with_vehicle_contract=(f.get("with_vehicle_contract") == "YES") if f.get("with_vehicle_contract") else None,
    )
    if include_conduction:
        d["conduction_number"] = f.get("conduction_number") or None
    return d


# ── Drivers ────────────────────────────────────────────────────────────────

@bp.route("/drivers")
@login_required
@require_permission("driver.view")
def driver_list():
    items = DriverService().list(include_inactive=True, user=current_user)
    return render_template("master_data/driver_list.html", items=items,
                           today=date.today())


@bp.route("/drivers/<int:did>")
@login_required
@require_permission("driver.view")
def driver_detail(did):
    item = DriverService().get_visible(did, current_user)
    if item is None:
        abort(403)
    attachments = _attachment_rows("drivers", did)
    return render_template("master_data/driver_detail.html",
                           item=item, driver=item, attachments=attachments,
                           today=date.today())


@bp.route("/drivers/new", methods=["GET", "POST"])
@login_required
@require_permission("driver.create")
def driver_new():
    departments = DepartmentService().list()
    license_types = LookupService().get_by_type("LICENSE_TYPE")
    if request.method == "POST":
        try:
            DriverService().create(**_driver_fields())
            flash("Driver created.", "success")
            return redirect(url_for("master_data.driver_list"))
        except (DuplicateDriverError, DateFormatError,
                RequiredFieldError) as e:
            flash(str(e), "danger")
    return render_template("master_data/driver_form.html",
                           item=None,
                           departments=departments,
                           license_types=license_types, title="New Driver")


@bp.route("/drivers/<int:did>/edit", methods=["GET", "POST"])
@login_required
@require_permission("driver.update")
def driver_edit(did):
    item = db.session.get(Driver, did)
    departments = DepartmentService().list()
    license_types = LookupService().get_by_type("LICENSE_TYPE")
    if request.method == "POST":
        try:
            DriverService().update(
                did,
                first_name=request.form.get("first_name", ""),
                last_name=request.form.get("last_name", ""),
                middle_name=request.form.get("middle_name", ""),
                license_expiry=parse_form_date(
                    request.form.get("license_expiry"), "License Expiry",
                    required=True),
                license_type=request.form.get("license_type", ""),
                phone=request.form.get("phone", ""),
                email=request.form.get("email", ""),
                branch_id=int(request.form["branch_id"]),
                department_id=int(request.form["department_id"]) if request.form.get("department_id") else None)
            flash("Driver updated.", "success")
            return redirect(url_for("master_data.driver_detail", did=did))
        except (DateFormatError, RequiredFieldError) as e:
            flash(str(e), "danger")
    return render_template("master_data/driver_form.html",
                           item=item,
                           departments=departments,
                           license_types=license_types,
                           title=f"Edit — {item.full_name}")


@bp.route("/drivers/<int:did>/deactivate", methods=["POST"])
@login_required
@require_permission("driver.delete")
def driver_deactivate(did):
    DriverService().deactivate(did)
    flash("Driver deactivated.", "info")
    return redirect(url_for("master_data.driver_list"))


def _driver_fields():
    f = request.form
    return dict(
        employee_number=f.get("employee_number", ""),
        first_name=f.get("first_name", ""),
        last_name=f.get("last_name", ""),
        middle_name=f.get("middle_name", ""),
        license_number=f.get("license_number", ""),
        license_expiry=parse_form_date(f.get("license_expiry"),
                                       "License Expiry", required=True),
        license_type=f.get("license_type", ""),
        branch_id=int(f["branch_id"]),
        department_id=int(f["department_id"]) if f.get("department_id") else None,
        phone=f.get("phone", ""),
        email=f.get("email", ""))


# ── Tires ──────────────────────────────────────────────────────────────────

@bp.route("/tires")
@login_required
@require_permission("tire.view")
def tire_list():
    items = TireService().list(include_inactive=True, user=current_user)
    return render_template("master_data/tire_list.html", items=items)


@bp.route("/tires/new", methods=["GET", "POST"])
@login_required
@require_permission("tire.create")
def tire_new():
    from app.modules.system_admin.services.lookup_service import LookupService
    tire_types = LookupService().get_by_type_with_fallback("TIRE_TYPE")
    if request.method == "POST":
        try:
            TireService().create(
                serial_number=request.form["serial_number"],
                brand=request.form["brand"], size=request.form["size"],
                tire_type=request.form["tire_type"],
                purchase_date=parse_form_date(
                    request.form.get("purchase_date"), "Purchase Date"),
                purchase_cost=request.form.get("purchase_cost") or None,
                vendor_id=int(request.form["vendor_id"]) if request.form.get("vendor_id") else None,
                branch_id=int(request.form["branch_id"]) if request.form.get("branch_id") else None)
            flash("Tire created.", "success")
            return redirect(url_for("master_data.tire_list"))
        except (DuplicateSerialError, DateFormatError,
                RequiredFieldError) as e:
            flash(str(e), "danger")
    return render_template("master_data/tire_form.html",
                           item=None, tire_types=tire_types,
                           title="New Tire")


@bp.route("/tires/<int:tid>/deactivate", methods=["POST"])
@login_required
@require_permission("tire.delete")
def tire_deactivate(tid):
    TireService().deactivate(tid)
    flash("Tire deactivated.", "info")
    return redirect(url_for("master_data.tire_list"))


# ── Batteries ──────────────────────────────────────────────────────────────

@bp.route("/batteries")
@login_required
@require_permission("battery.view")
def battery_list():
    items = BatteryService().list(include_inactive=True, user=current_user)
    return render_template("master_data/battery_list.html", items=items)


@bp.route("/batteries/new", methods=["GET", "POST"])
@login_required
@require_permission("battery.create")
def battery_new():
    if request.method == "POST":
        try:
            BatteryService().create(
                serial_number=request.form["serial_number"],
                brand=request.form["brand"],
                capacity_ah=int(request.form["capacity_ah"]) if request.form.get("capacity_ah") else None,
                voltage=int(request.form["voltage"]) if request.form.get("voltage") else None,
                purchase_date=parse_form_date(
                    request.form.get("purchase_date"), "Purchase Date"),
                purchase_cost=request.form.get("purchase_cost") or None,
                vendor_id=int(request.form["vendor_id"]) if request.form.get("vendor_id") else None,
                branch_id=int(request.form["branch_id"]) if request.form.get("branch_id") else None)
            flash("Battery created.", "success")
            return redirect(url_for("master_data.battery_list"))
        except Exception as e:
            flash(str(e), "danger")
    return render_template("master_data/battery_form.html",
                           item=None, title="New Battery")


@bp.route("/batteries/<int:bid>/deactivate", methods=["POST"])
@login_required
@require_permission("battery.delete")
def battery_deactivate(bid):
    BatteryService().deactivate(bid)
    flash("Battery deactivated.", "info")
    return redirect(url_for("master_data.battery_list"))


# ── Attachments ────────────────────────────────────────────────────────────

@bp.route("/attachments/list")
@login_required
def attachment_list_json():
    ref_table = request.args.get("reference_table")
    ref_id = request.args.get("reference_id")
    items = _attachment_rows(ref_table, int(ref_id)) if ref_table and ref_id else []
    return jsonify(attachments=[{
        "id": a.id, "filename": a.original_filename,
        "size": a.file_size, "mime_type": a.mime_type,
        "is_image": bool(a.mime_type and a.mime_type.startswith("image/")),
        "view_url": url_for("master_data.attachment_view", att_id=a.id),
        "download_url": url_for("master_data.attachment_download", att_id=a.id),
    } for a in items])


@bp.route("/attachments/upload", methods=["POST"])
@login_required
def attachment_upload():
    """Deliberately does NOT use @require_permission — that decorator's
    abort(403) renders an HTML error page, which the frontend's
    `r.json()` call can't parse, silently collapsing into a generic
    "Upload failed. Please try again." with zero indication of the real
    cause. Every failure path here returns real JSON instead, and
    unexpected exceptions are logged in full server-side (for admins)
    while the client only sees a safe, friendly message."""
    if not current_user.has_permission("attachment.upload"):
        return jsonify(ok=False,
                       error="You don't have permission to upload attachments."), 403
    ref_table = request.form.get("reference_table")
    ref_id = request.form.get("reference_id")
    file = request.files.get("file")
    if not ref_table or not ref_id:
        return jsonify(ok=False,
                       error="Missing reference information for this upload."), 400
    try:
        ref_id = int(ref_id)
    except (TypeError, ValueError):
        return jsonify(ok=False, error="Invalid reference ID."), 400

    try:
        att = AttachmentService().upload(file, ref_table, ref_id, user=current_user)
        db.session.commit()
        return jsonify(ok=True, id=att.id,
                       filename=att.original_filename,
                       size=att.file_size,
                       mime_type=att.mime_type,
                       is_image=bool(att.mime_type and
                                    att.mime_type.startswith("image/")),
                       view_url=url_for("master_data.attachment_view",
                                        att_id=att.id),
                       download_url=url_for("master_data.attachment_download",
                                            att_id=att.id))
    except AttachmentError as e:
        return jsonify(ok=False, error=str(e)), 400
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception(
            "Unexpected error uploading attachment (reference_table=%s, "
            "reference_id=%s, user=%s): %s",
            ref_table, ref_id, current_user.id, e)
        return jsonify(
            ok=False,
            error="The file could not be uploaded due to a server error. "
                 "Please try again, or contact your administrator if this "
                 "keeps happening."), 500


@bp.route("/attachments/<int:att_id>/delete", methods=["POST"])
@login_required
@require_permission("attachment.delete")
def attachment_delete(att_id):
    AttachmentService().delete(att_id, user=current_user)
    return jsonify(ok=True)


@bp.route("/attachments/<int:att_id>/download")
@login_required
def attachment_download(att_id):
    att = db.session.get(Attachment, att_id)
    if att is None or not att.is_active:
        flash("Attachment not found.", "warning")
        return redirect(url_for("main.dashboard"))
    upload_dir = os.path.join(current_app.instance_path, "uploads",
                              att.reference_table)
    return send_from_directory(upload_dir, att.filename,
                               download_name=att.original_filename)


@bp.route("/attachments/<int:att_id>/view")
@login_required
def attachment_view(att_id):
    """Serve the file inline (not as a download) so images can be
    previewed/embedded directly in the browser (e.g. <img> tags)."""
    att = db.session.get(Attachment, att_id)
    if att is None or not att.is_active:
        flash("Attachment not found.", "warning")
        return redirect(url_for("main.dashboard"))
    upload_dir = os.path.join(current_app.instance_path, "uploads",
                              att.reference_table)
    return send_from_directory(upload_dir, att.filename, as_attachment=False,
                               mimetype=att.mime_type)
