"""Master Data blueprint — all 10 modules (org, reference, asset masters).
Thin controllers: parse → service → render. All business logic in services."""
from datetime import date

from flask import (Blueprint, render_template, redirect, url_for,
                   flash, request, jsonify, send_from_directory, current_app)
from flask_login import login_required, current_user

from app.core.security.decorators import require_permission
from app.core.security.registry import registry
from app.core.attachments.service import AttachmentService, AttachmentError
from app.core.attachments.models import Attachment
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
    VehicleService, DuplicateVehicleError)
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
             "vehicletype", "maintenancetype"]:
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
    ("PROFESSIONAL", "Professional", 1),
    ("NON_PROFESSIONAL", "Non-Professional", 2),
    ("STUDENT_PERMIT", "Student Permit", 3),
]:
    lookup_registry.register("LICENSE_TYPE", _code, _desc, _order)


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
    branches = BranchService().list()
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
                           item=None, branches=branches, title="New Department")


@bp.route("/departments/<int:did>/edit", methods=["GET", "POST"])
@login_required
@require_permission("department.update")
def department_edit(did):
    item = db.session.get(Department, did)
    branches = BranchService().list()
    if request.method == "POST":
        DepartmentService().update(
            did, name=request.form["name"],
            description=request.form.get("description", ""))
        flash("Department updated.", "success")
        return redirect(url_for("master_data.department_list"))
    return render_template("master_data/department_form.html",
                           item=item, branches=branches,
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
                           item=None, title="New Vehicle Type")


@bp.route("/vehicle-types/<int:vid>/edit", methods=["GET", "POST"])
@login_required
@require_permission("vehicletype.update")
def vehicletype_edit(vid):
    item = db.session.get(VehicleType, vid)
    if request.method == "POST":
        VehicleTypeService().update(
            vid, name=request.form["name"],
            category=request.form["category"],
            description=request.form.get("description", ""))
        flash("Vehicle type updated.", "success")
        return redirect(url_for("master_data.vehicletype_list"))
    return render_template("master_data/vehicletype_form.html",
                           item=item, title=f"Edit — {item.code}")


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
                interval_km=request.form.get("interval_km") or None,
                interval_days=request.form.get("interval_days") or None,
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
            interval_km=request.form.get("interval_km") or None,
            interval_days=request.form.get("interval_days") or None,
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
    items = VendorService().list(include_inactive=True)
    return render_template("master_data/vendor_list.html", items=items)


@bp.route("/vendors/new", methods=["GET", "POST"])
@login_required
@require_permission("vendor.create")
def vendor_new():
    if request.method == "POST":
        try:
            VendorService().create(**_vendor_fields())
            flash("Vendor created.", "success")
            return redirect(url_for("master_data.vendor_list"))
        except Exception as e:
            flash(str(e), "danger")
    return render_template("master_data/vendor_form.html",
                           item=None, title="New Vendor")


@bp.route("/vendors/<int:vid>/edit", methods=["GET", "POST"])
@login_required
@require_permission("vendor.update")
def vendor_edit(vid):
    item = db.session.get(Vendor, vid)
    if request.method == "POST":
        VendorService().update(vid, **_vendor_fields(include_code=False))
        flash("Vendor updated.", "success")
        return redirect(url_for("master_data.vendor_list"))
    return render_template("master_data/vendor_form.html",
                           item=item, title=f"Edit — {item.code}")


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


# ── Vehicles ───────────────────────────────────────────────────────────────

@bp.route("/vehicles")
@login_required
@require_permission("vehicle.view")
def vehicle_list():
    items = VehicleService().list(include_inactive=True)
    return render_template("master_data/vehicle_list.html", items=items)


@bp.route("/vehicles/<int:vid>")
@login_required
@require_permission("vehicle.view")
def vehicle_detail(vid):
    item = db.session.get(Vehicle, vid)
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


@bp.route("/vehicles/new", methods=["GET", "POST"])
@login_required
@require_permission("vehicle.create")
def vehicle_new():
    vtypes = VehicleTypeService().list()
    branches = BranchService().list()
    departments = DepartmentService().list()
    bus = BusinessUnitService().list()
    fuel_types = LookupService().get_by_type("FUEL_TYPE")
    if request.method == "POST":
        try:
            VehicleService().create(**_vehicle_fields())
            flash("Vehicle created.", "success")
            return redirect(url_for("master_data.vehicle_list"))
        except DuplicateVehicleError as e:
            flash(str(e), "danger")
    return render_template("master_data/vehicle_form.html",
                           item=None, vtypes=vtypes, vehicle_types=vtypes,
                           branches=branches, departments=departments,
                           bus=bus, fuel_types=fuel_types,
                           title="New Vehicle")


@bp.route("/vehicles/<int:vid>/edit", methods=["GET", "POST"])
@login_required
@require_permission("vehicle.update")
def vehicle_edit(vid):
    item = db.session.get(Vehicle, vid)
    vtypes = VehicleTypeService().list()
    branches = BranchService().list()
    departments = DepartmentService().list()
    bus = BusinessUnitService().list()
    fuel_types = LookupService().get_by_type("FUEL_TYPE")
    if request.method == "POST":
        VehicleService().update(vid, **_vehicle_fields(include_conduction=False))
        flash("Vehicle updated.", "success")
        return redirect(url_for("master_data.vehicle_detail", vid=vid))
    return render_template("master_data/vehicle_form.html",
                           item=item, vtypes=vtypes, vehicle_types=vtypes,
                           branches=branches, departments=departments,
                           bus=bus, fuel_types=fuel_types,
                           title=f"Edit — {item.conduction_number or item.plate_number}")


@bp.route("/vehicles/<int:vid>/deactivate", methods=["POST"])
@login_required
@require_permission("vehicle.delete")
def vehicle_deactivate(vid):
    VehicleService().deactivate(vid)
    flash("Vehicle deactivated.", "info")
    return redirect(url_for("master_data.vehicle_list"))


def _vehicle_fields(include_conduction=True):
    f = request.form
    d = dict(
        vehicle_type_id=int(f["vehicle_type_id"]),
        brand=f.get("brand", ""),
        model=f.get("model", ""),
        year=int(f.get("year", 2024)),
        color=f.get("color", ""),
        fuel_type=f.get("fuel_type", ""),
        branch_id=int(f["branch_id"]),
        department_id=int(f["department_id"]) if f.get("department_id") else None,
        business_unit_id=int(f["business_unit_id"]) if f.get("business_unit_id") else None,
        chassis_number=f.get("chassis_number") or None,
        engine_number=f.get("engine_number") or None,
        plate_number=f.get("plate_number") or None,
        acquisition_date=date.fromisoformat(f["acquisition_date"]) if f.get("acquisition_date") else None,
        acquisition_cost=f.get("acquisition_cost") or None,
        current_odometer=int(f.get("current_odometer") or 0),
        notes=f.get("notes", ""))
    if include_conduction:
        d["conduction_number"] = f.get("conduction_number") or None
    return d


# ── Drivers ────────────────────────────────────────────────────────────────

@bp.route("/drivers")
@login_required
@require_permission("driver.view")
def driver_list():
    items = DriverService().list(include_inactive=True)
    return render_template("master_data/driver_list.html", items=items,
                           today=date.today())


@bp.route("/drivers/<int:did>")
@login_required
@require_permission("driver.view")
def driver_detail(did):
    item = db.session.get(Driver, did)
    attachments = _attachment_rows("drivers", did)
    return render_template("master_data/driver_detail.html",
                           item=item, driver=item, attachments=attachments,
                           today=date.today())


@bp.route("/drivers/new", methods=["GET", "POST"])
@login_required
@require_permission("driver.create")
def driver_new():
    branches = BranchService().list()
    departments = DepartmentService().list()
    license_types = LookupService().get_by_type("LICENSE_TYPE")
    if request.method == "POST":
        try:
            DriverService().create(**_driver_fields())
            flash("Driver created.", "success")
            return redirect(url_for("master_data.driver_list"))
        except DuplicateDriverError as e:
            flash(str(e), "danger")
    return render_template("master_data/driver_form.html",
                           item=None, branches=branches,
                           departments=departments,
                           license_types=license_types, title="New Driver")


@bp.route("/drivers/<int:did>/edit", methods=["GET", "POST"])
@login_required
@require_permission("driver.update")
def driver_edit(did):
    item = db.session.get(Driver, did)
    branches = BranchService().list()
    departments = DepartmentService().list()
    license_types = LookupService().get_by_type("LICENSE_TYPE")
    if request.method == "POST":
        DriverService().update(
            did,
            first_name=request.form.get("first_name", ""),
            last_name=request.form.get("last_name", ""),
            middle_name=request.form.get("middle_name", ""),
            license_expiry=date.fromisoformat(request.form["license_expiry"]),
            license_type=request.form.get("license_type", ""),
            phone=request.form.get("phone", ""),
            email=request.form.get("email", ""),
            branch_id=int(request.form["branch_id"]),
            department_id=int(request.form["department_id"]) if request.form.get("department_id") else None)
        flash("Driver updated.", "success")
        return redirect(url_for("master_data.driver_detail", did=did))
    return render_template("master_data/driver_form.html",
                           item=item, branches=branches,
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
        license_expiry=date.fromisoformat(f["license_expiry"]),
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
    items = TireService().list(include_inactive=True)
    return render_template("master_data/tire_list.html", items=items)


@bp.route("/tires/new", methods=["GET", "POST"])
@login_required
@require_permission("tire.create")
def tire_new():
    vendors = VendorService().list()
    if request.method == "POST":
        try:
            TireService().create(
                serial_number=request.form["serial_number"],
                brand=request.form["brand"], size=request.form["size"],
                tire_type=request.form["tire_type"],
                purchase_date=date.fromisoformat(request.form["purchase_date"]) if request.form.get("purchase_date") else None,
                purchase_cost=request.form.get("purchase_cost") or None,
                vendor_id=int(request.form["vendor_id"]) if request.form.get("vendor_id") else None)
            flash("Tire created.", "success")
            return redirect(url_for("master_data.tire_list"))
        except DuplicateSerialError as e:
            flash(str(e), "danger")
    return render_template("master_data/tire_form.html",
                           item=None, vendors=vendors, title="New Tire")


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
    items = BatteryService().list(include_inactive=True)
    return render_template("master_data/battery_list.html", items=items)


@bp.route("/batteries/new", methods=["GET", "POST"])
@login_required
@require_permission("battery.create")
def battery_new():
    vendors = VendorService().list()
    if request.method == "POST":
        try:
            BatteryService().create(
                serial_number=request.form["serial_number"],
                brand=request.form["brand"],
                capacity_ah=int(request.form["capacity_ah"]) if request.form.get("capacity_ah") else None,
                voltage=int(request.form["voltage"]) if request.form.get("voltage") else None,
                purchase_date=date.fromisoformat(request.form["purchase_date"]) if request.form.get("purchase_date") else None,
                purchase_cost=request.form.get("purchase_cost") or None,
                vendor_id=int(request.form["vendor_id"]) if request.form.get("vendor_id") else None)
            flash("Battery created.", "success")
            return redirect(url_for("master_data.battery_list"))
        except Exception as e:
            flash(str(e), "danger")
    return render_template("master_data/battery_form.html",
                           item=None, vendors=vendors, title="New Battery")


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
@require_permission("attachment.upload")
def attachment_upload():
    ref_table = request.form.get("reference_table")
    ref_id = request.form.get("reference_id")
    file = request.files.get("file")
    try:
        att = AttachmentService().upload(
            file, ref_table, int(ref_id), user=current_user)
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
