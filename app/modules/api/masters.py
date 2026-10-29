"""Simple master-data CRUD for React: branches, departments, business units,
vehicle types, vehicle brands.

Each entity follows the same list/get/create/update/deactivate envelope used
by tires/batteries so the frontend list scaffold is reusable.
"""
from flask import jsonify, request

from app.modules.api.auth import api_auth_required
from app.modules.api.routes import bp


def _bad(message, code=400):
    return jsonify({"error": "bad_request", "message": message}), code


def _not_found(label="Record"):
    return jsonify({"error": "not_found",
                    "message": f"{label} not found."}), 404


def _validation(message, field="_"):
    return jsonify({"error": "validation", "message": message,
                    "fields": {field: message}}), 400


def _page(rows):
    try:
        page = int(request.args.get("page", 1))
        page_size = int(request.args.get("page_size", 50))
    except (TypeError, ValueError):
        return None, _bad("page and page_size must be integers.")
    if page < 1 or page_size < 1:
        return None, _bad("page and page_size must be 1 or greater.")
    page_size = min(page_size, 200)
    total = len(rows)
    start = (page - 1) * page_size
    return {
        "items": rows[start:start + page_size],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, (total + page_size - 1) // page_size),
    }, None


# ── Branches ────────────────────────────────────────────────────────────────

def _branch_json(b):
    # company/region/area_district are RESOLVED from the Company record,
    # never stored on the branch -- same rule as Vehicle (Stage 3A), so
    # they can never drift from the master. company_id alone would be
    # ambiguous in a UI: per D2 several Companies share one short_name
    # and differ only by Region + Area/District.
    co = b.company
    return {
        "id": b.id,
        "code": b.code,
        "name": b.name,
        "address": b.address,
        "city": b.city,
        "phone": b.phone,
        "email": b.email,
        "company_id": b.company_id,
        "company": co.short_name if co else None,
        "region": co.region.name if co and co.region else None,
        "area_district": (co.area_district.name
                          if co and co.area_district else None),
        "is_active": bool(b.is_active),
    }


@bp.route("/branches", methods=["GET"])
@api_auth_required("branch.view")
def list_branches(api_user):
    from app.modules.master_data.org.service import BranchService
    q = (request.args.get("q") or "").strip().lower()
    rows = BranchService().list(include_inactive=True)
    if q:
        rows = [b for b in rows if q in " ".join(filter(None, [
            b.code, b.name, b.city, b.phone])).lower()]
    payload, err = _page([_branch_json(b) for b in rows])
    return err if err else jsonify(payload)


@bp.route("/branches/<int:branch_id>", methods=["GET"])
@api_auth_required("branch.view")
def get_branch(api_user, branch_id):
    from app.modules.master_data.org.service import BranchService
    b = BranchService().get(branch_id)
    if b is None:
        return _not_found("Branch")
    return jsonify(_branch_json(b))


@bp.route("/branches", methods=["POST"])
@api_auth_required("branch.create")
def create_branch(api_user):
    from app.modules.master_data.org.service import (
        BranchService, DuplicateCodeError, InvalidOrgDataError)
    p = request.get_json(silent=True) or {}
    code = (p.get("code") or "").strip()
    name = (p.get("name") or "").strip()
    if not code:
        return _validation("code is required.", "code")
    if not name:
        return _validation("name is required.", "name")
    try:
        b = BranchService().create(
            code=code, name=name,
            address=(p.get("address") or None),
            city=(p.get("city") or None),
            phone=(p.get("phone") or None),
            email=(p.get("email") or None),
            # The browser form sends ids as strings -- coerce, same as
            # every other id on this blueprint (see v278).
            company_id=(int(p["company_id"]) if p.get("company_id")
                        else None))
    except DuplicateCodeError as e:
        return _validation(str(e), "code")
    except InvalidOrgDataError as e:
        return _validation(str(e), "company_id")
    return jsonify(_branch_json(b)), 201


@bp.route("/branches/<int:branch_id>", methods=["PUT", "PATCH"])
@api_auth_required("branch.update")
def update_branch(api_user, branch_id):
    from app.modules.master_data.org.service import (
        BranchService, InvalidOrgDataError)
    p = request.get_json(silent=True) or {}
    fields = {}
    for k in ("code", "name", "address", "city", "phone", "email"):
        if k in p:
            fields[k] = (p[k].strip() if isinstance(p[k], str) else p[k]) or None
    # Sent explicitly so a blank CLEARS the mapping -- correcting a
    # wrongly-mapped branch has to be possible, not silently ignored.
    if "company_id" in p:
        fields["company_id"] = (int(p["company_id"]) if p["company_id"]
                                else None)
    try:
        b = BranchService().update(branch_id, **fields)
    except InvalidOrgDataError as e:
        return _validation(str(e), "company_id")
    if b is None:
        return _not_found("Branch")
    return jsonify(_branch_json(b))


@bp.route("/branches/<int:branch_id>/deactivate", methods=["POST"])
@api_auth_required("branch.delete")
def deactivate_branch(api_user, branch_id):
    from app.modules.master_data.org.service import BranchService
    if BranchService().get(branch_id) is None:
        return _not_found("Branch")
    BranchService().deactivate(branch_id)
    return jsonify({"ok": True})


# ── Departments ─────────────────────────────────────────────────────────────

def _dept_json(d):
    return {
        "id": d.id,
        "code": d.code,
        "name": d.name,
        "branch_id": d.branch_id,
        "branch": d.branch.name if d.branch else None,
        "cost_center": d.cost_center,
        "description": d.description,
        "is_active": bool(d.is_active),
    }


@bp.route("/departments", methods=["GET"])
@api_auth_required("department.view")
def list_departments(api_user):
    from app.modules.master_data.org.service import DepartmentService
    q = (request.args.get("q") or "").strip().lower()
    try:
        branch_id = request.args.get("branch_id")
        branch_id = int(branch_id) if branch_id else None
    except (TypeError, ValueError):
        return _bad("branch_id must be an integer.")
    rows = DepartmentService().list(include_inactive=True)
    if branch_id:
        rows = [d for d in rows if d.branch_id == branch_id]
    if q:
        rows = [d for d in rows if q in " ".join(filter(None, [
            d.code, d.name, d.cost_center,
            d.branch.name if d.branch else None])).lower()]
    payload, err = _page([_dept_json(d) for d in rows])
    return err if err else jsonify(payload)


@bp.route("/departments/<int:dept_id>", methods=["GET"])
@api_auth_required("department.view")
def get_department(api_user, dept_id):
    from app.modules.master_data.org.service import DepartmentService
    d = DepartmentService().get(dept_id)
    if d is None:
        return _not_found("Department")
    return jsonify(_dept_json(d))


@bp.route("/departments", methods=["POST"])
@api_auth_required("department.create")
def create_department(api_user):
    from app.modules.master_data.org.service import (
        DepartmentService, DuplicateCodeError)
    p = request.get_json(silent=True) or {}
    code = (p.get("code") or "").strip()
    name = (p.get("name") or "").strip()
    try:
        branch_id = int(p["branch_id"]) if p.get("branch_id") not in (None, "") else None
    except (TypeError, ValueError):
        return _validation("branch_id must be an integer.", "branch_id")
    if not code:
        return _validation("code is required.", "code")
    if not name:
        return _validation("name is required.", "name")
    if not branch_id:
        return _validation("branch_id is required.", "branch_id")
    try:
        d = DepartmentService().create(
            code=code, name=name, branch_id=branch_id,
            cost_center=(p.get("cost_center") or None),
            description=(p.get("description") or None))
    except DuplicateCodeError as e:
        return _validation(str(e), "code")
    return jsonify(_dept_json(d)), 201


@bp.route("/departments/<int:dept_id>", methods=["PUT", "PATCH"])
@api_auth_required("department.update")
def update_department(api_user, dept_id):
    from app.modules.master_data.org.service import DepartmentService
    p = request.get_json(silent=True) or {}
    fields = {}
    for k in ("code", "name", "cost_center", "description"):
        if k in p:
            fields[k] = (p[k].strip() if isinstance(p[k], str) else p[k]) or None
    if "branch_id" in p and p["branch_id"] not in (None, ""):
        try:
            fields["branch_id"] = int(p["branch_id"])
        except (TypeError, ValueError):
            return _validation("branch_id must be an integer.", "branch_id")
    d = DepartmentService().update(dept_id, **fields)
    if d is None:
        return _not_found("Department")
    return jsonify(_dept_json(d))


@bp.route("/departments/<int:dept_id>/deactivate", methods=["POST"])
@api_auth_required("department.delete")
def deactivate_department(api_user, dept_id):
    from app.modules.master_data.org.service import DepartmentService
    if DepartmentService().get(dept_id) is None:
        return _not_found("Department")
    DepartmentService().deactivate(dept_id)
    return jsonify({"ok": True})


# ── Business Units ──────────────────────────────────────────────────────────

def _bu_json(b):
    return {
        "id": b.id,
        "code": b.code,
        "name": b.name,
        "description": b.description,
        "is_active": bool(b.is_active),
    }


@bp.route("/business-units", methods=["GET"])
@api_auth_required("businessunit.view")
def list_business_units(api_user):
    from app.modules.master_data.org.service import BusinessUnitService
    q = (request.args.get("q") or "").strip().lower()
    rows = BusinessUnitService().list(include_inactive=True)
    if q:
        rows = [b for b in rows if q in " ".join(filter(None, [
            b.code, b.name, b.description])).lower()]
    payload, err = _page([_bu_json(b) for b in rows])
    return err if err else jsonify(payload)


@bp.route("/business-units/<int:bu_id>", methods=["GET"])
@api_auth_required("businessunit.view")
def get_business_unit(api_user, bu_id):
    from app.modules.master_data.org.service import BusinessUnitService
    b = BusinessUnitService().get(bu_id)
    if b is None:
        return _not_found("Business unit")
    return jsonify(_bu_json(b))


@bp.route("/business-units", methods=["POST"])
@api_auth_required("businessunit.create")
def create_business_unit(api_user):
    from app.modules.master_data.org.service import (
        BusinessUnitService, DuplicateCodeError)
    p = request.get_json(silent=True) or {}
    code = (p.get("code") or "").strip()
    name = (p.get("name") or "").strip()
    if not code:
        return _validation("code is required.", "code")
    if not name:
        return _validation("name is required.", "name")
    try:
        b = BusinessUnitService().create(
            code=code, name=name,
            description=(p.get("description") or None))
    except DuplicateCodeError as e:
        return _validation(str(e), "code")
    return jsonify(_bu_json(b)), 201


@bp.route("/business-units/<int:bu_id>", methods=["PUT", "PATCH"])
@api_auth_required("businessunit.update")
def update_business_unit(api_user, bu_id):
    from app.modules.master_data.org.service import BusinessUnitService
    p = request.get_json(silent=True) or {}
    fields = {}
    for k in ("code", "name", "description"):
        if k in p:
            fields[k] = (p[k].strip() if isinstance(p[k], str) else p[k]) or None
    b = BusinessUnitService().update(bu_id, **fields)
    if b is None:
        return _not_found("Business unit")
    return jsonify(_bu_json(b))


@bp.route("/business-units/<int:bu_id>/deactivate", methods=["POST"])
@api_auth_required("businessunit.delete")
def deactivate_business_unit(api_user, bu_id):
    from app.modules.master_data.org.service import BusinessUnitService
    if BusinessUnitService().get(bu_id) is None:
        return _not_found("Business unit")
    BusinessUnitService().deactivate(bu_id)
    return jsonify({"ok": True})


# ── Vehicle Types ───────────────────────────────────────────────────────────

def _vtype_json(t):
    return {
        "id": t.id,
        "code": t.code,
        "name": t.name,
        "category": t.category,
        "description": t.description,
        "is_active": bool(t.is_active),
    }


@bp.route("/vehicle-types", methods=["GET"])
@api_auth_required("vehicletype.view")
def list_vehicle_types(api_user):
    from app.modules.master_data.reference.service import VehicleTypeService
    q = (request.args.get("q") or "").strip().lower()
    rows = VehicleTypeService().list(include_inactive=True)
    if q:
        rows = [t for t in rows if q in " ".join(filter(None, [
            t.code, t.name, t.category, t.description])).lower()]
    payload, err = _page([_vtype_json(t) for t in rows])
    return err if err else jsonify(payload)


@bp.route("/vehicle-types/<int:type_id>", methods=["GET"])
@api_auth_required("vehicletype.view")
def get_vehicle_type(api_user, type_id):
    from app.modules.master_data.reference.service import VehicleTypeService
    t = VehicleTypeService().get(type_id)
    if t is None:
        return _not_found("Vehicle type")
    return jsonify(_vtype_json(t))


@bp.route("/vehicle-types", methods=["POST"])
@api_auth_required("vehicletype.create")
def create_vehicle_type(api_user):
    from app.modules.master_data.reference.service import (
        VehicleTypeService, DuplicateCodeError)
    p = request.get_json(silent=True) or {}
    code = (p.get("code") or "").strip()
    name = (p.get("name") or "").strip()
    category = (p.get("category") or "").strip()
    if not code:
        return _validation("code is required.", "code")
    if not name:
        return _validation("name is required.", "name")
    if not category:
        return _validation("category is required.", "category")
    try:
        t = VehicleTypeService().create(
            code=code, name=name, category=category,
            description=(p.get("description") or None))
    except DuplicateCodeError as e:
        return _validation(str(e), "code")
    return jsonify(_vtype_json(t)), 201


@bp.route("/vehicle-types/<int:type_id>", methods=["PUT", "PATCH"])
@api_auth_required("vehicletype.update")
def update_vehicle_type(api_user, type_id):
    from app.modules.master_data.reference.service import VehicleTypeService
    p = request.get_json(silent=True) or {}
    fields = {}
    for k in ("name", "category", "description"):
        if k in p:
            fields[k] = (p[k].strip() if isinstance(p[k], str) else p[k]) or None
    t = VehicleTypeService().update(type_id, **fields)
    if t is None:
        return _not_found("Vehicle type")
    return jsonify(_vtype_json(t))


@bp.route("/vehicle-types/<int:type_id>/deactivate", methods=["POST"])
@api_auth_required("vehicletype.delete")
def deactivate_vehicle_type(api_user, type_id):
    from app.modules.master_data.reference.service import VehicleTypeService
    if VehicleTypeService().get(type_id) is None:
        return _not_found("Vehicle type")
    VehicleTypeService().deactivate(type_id)
    return jsonify({"ok": True})


# ── Vehicle Brands ──────────────────────────────────────────────────────────

def _brand_json(b, model_count=0):
    return {
        "id": b.id,
        "name": b.name,
        "model_count": int(model_count or 0),
        "is_active": bool(b.is_active),
    }


@bp.route("/vehicle-brands", methods=["GET"])
@api_auth_required("vehiclebrand.view")
def list_vehicle_brands_master(api_user):
    """Full master list (includes inactive). Distinct from
    /reference/vehicle-brands which is the form dropdown."""
    from app.modules.master_data.vehicle_brand.service import VehicleBrandService
    q = (request.args.get("q") or "").strip().lower()
    rows = VehicleBrandService().list(include_inactive=True)
    if q:
        rows = [b for b in rows if q in (b.name or "").lower()]
    from sqlalchemy import func
    from app.extensions import db
    from app.modules.master_data.vehicle_brand.models import VehicleModel
    counts = dict(
        db.session.query(VehicleModel.brand_id, func.count(VehicleModel.id))
        .group_by(VehicleModel.brand_id)
        .all()
    )
    payload, err = _page([_brand_json(b, counts.get(b.id, 0)) for b in rows])
    return err if err else jsonify(payload)


@bp.route("/vehicle-brands/<int:brand_id>", methods=["GET"])
@api_auth_required("vehiclebrand.view")
def get_vehicle_brand(api_user, brand_id):
    from app.modules.master_data.vehicle_brand.service import VehicleBrandService
    from app.modules.master_data.vehicle_brand.models import VehicleBrand
    from app.extensions import db
    b = db.session.get(VehicleBrand, brand_id)
    if b is None:
        return _not_found("Vehicle brand")
    return jsonify(_brand_json(b))


@bp.route("/vehicle-brands", methods=["POST"])
@api_auth_required("vehiclebrand.create")
def create_vehicle_brand(api_user):
    from app.modules.master_data.vehicle_brand.service import (
        VehicleBrandService, DuplicateBrandError)
    p = request.get_json(silent=True) or {}
    name = (p.get("name") or "").strip()
    if not name:
        return _validation("name is required.", "name")
    try:
        b = VehicleBrandService().create(name=name)
    except DuplicateBrandError as e:
        return _validation(str(e), "name")
    return jsonify(_brand_json(b)), 201


@bp.route("/vehicle-brands/<int:brand_id>", methods=["PUT", "PATCH"])
@api_auth_required("vehiclebrand.update")
def update_vehicle_brand(api_user, brand_id):
    from app.modules.master_data.vehicle_brand.service import (
        VehicleBrandService, DuplicateBrandError)
    p = request.get_json(silent=True) or {}
    name = (p.get("name") or "").strip()
    if not name:
        return _validation("name is required.", "name")
    try:
        b = VehicleBrandService().update(brand_id, name=name)
    except DuplicateBrandError as e:
        return _validation(str(e), "name")
    if b is None:
        return _not_found("Vehicle brand")
    return jsonify(_brand_json(b))


@bp.route("/vehicle-brands/<int:brand_id>/deactivate", methods=["POST"])
@api_auth_required("vehiclebrand.delete")
def deactivate_vehicle_brand(api_user, brand_id):
    from app.modules.master_data.vehicle_brand.service import VehicleBrandService
    from app.modules.master_data.vehicle_brand.models import VehicleBrand
    from app.extensions import db
    if db.session.get(VehicleBrand, brand_id) is None:
        return _not_found("Vehicle brand")
    VehicleBrandService().deactivate(brand_id)
    return jsonify({"ok": True})


# ── Vehicle Models ──────────────────────────────────────────────────────────

def _model_json(m):
    return {
        "id": m.id,
        "name": m.name,
        "brand_id": m.brand_id,
        "brand": m.brand.name if m.brand else None,
        "is_active": bool(m.is_active),
    }


@bp.route("/vehicle-models", methods=["GET"])
@api_auth_required("vehiclemodel.view")
def list_vehicle_models(api_user):
    from app.modules.master_data.vehicle_brand.service import VehicleModelService
    q = (request.args.get("q") or "").strip().lower()
    try:
        brand_id = request.args.get("brand_id")
        brand_id = int(brand_id) if brand_id else None
    except (TypeError, ValueError):
        return _bad("brand_id must be an integer.")
    rows = VehicleModelService().list(brand_id=brand_id, include_inactive=True)
    if q:
        rows = [m for m in rows if q in " ".join(filter(None, [
            m.name, m.brand.name if m.brand else None])).lower()]
    payload, err = _page([_model_json(m) for m in rows])
    return err if err else jsonify(payload)


@bp.route("/vehicle-models/<int:model_id>", methods=["GET"])
@api_auth_required("vehiclemodel.view")
def get_vehicle_model(api_user, model_id):
    from app.modules.master_data.vehicle_brand.models import VehicleModel
    from app.extensions import db
    m = db.session.get(VehicleModel, model_id)
    if m is None:
        return _not_found("Vehicle model")
    return jsonify(_model_json(m))


@bp.route("/vehicle-models", methods=["POST"])
@api_auth_required("vehiclemodel.create")
def create_vehicle_model(api_user):
    from app.modules.master_data.vehicle_brand.service import (
        VehicleModelService, DuplicateModelError)
    p = request.get_json(silent=True) or {}
    name = (p.get("name") or "").strip()
    try:
        brand_id = int(p["brand_id"]) if p.get("brand_id") not in (None, "") else None
    except (TypeError, ValueError):
        return _validation("brand_id must be an integer.", "brand_id")
    if not name:
        return _validation("name is required.", "name")
    if not brand_id:
        return _validation("brand_id is required.", "brand_id")
    try:
        m = VehicleModelService().create(brand_id=brand_id, name=name)
    except DuplicateModelError as e:
        return _validation(str(e), "name")
    return jsonify(_model_json(m)), 201


@bp.route("/vehicle-models/<int:model_id>", methods=["PUT", "PATCH"])
@api_auth_required("vehiclemodel.update")
def update_vehicle_model(api_user, model_id):
    from app.modules.master_data.vehicle_brand.service import (
        VehicleModelService, DuplicateModelError)
    p = request.get_json(silent=True) or {}
    name = (p.get("name") or "").strip()
    if not name:
        return _validation("name is required.", "name")
    try:
        m = VehicleModelService().update(model_id, name=name)
    except DuplicateModelError as e:
        return _validation(str(e), "name")
    if m is None:
        return _not_found("Vehicle model")
    return jsonify(_model_json(m))


@bp.route("/vehicle-models/<int:model_id>/deactivate", methods=["POST"])
@api_auth_required("vehiclemodel.delete")
def deactivate_vehicle_model(api_user, model_id):
    from app.modules.master_data.vehicle_brand.service import VehicleModelService
    from app.modules.master_data.vehicle_brand.models import VehicleModel
    from app.extensions import db
    if db.session.get(VehicleModel, model_id) is None:
        return _not_found("Vehicle model")
    VehicleModelService().deactivate(model_id)
    return jsonify({"ok": True})


# ── Maintenance Types ───────────────────────────────────────────────────────

def _mtype_json(t, pm_template_count=0):
    return {
        "id": t.id,
        "code": t.code,
        "name": t.name,
        "category": t.category,
        "description": t.description,
        # TIRE | BATTERY | None -- shows the MO's Tire & Battery Work.
        "equipment_kind": getattr(t, "equipment_kind", None),
        "pm_template_count": int(pm_template_count or 0),
        "is_active": bool(t.is_active),
    }


@bp.route("/maintenance-types", methods=["GET"])
@api_auth_required("maintenancetype.view")
def list_maintenance_types(api_user):
    from app.modules.master_data.reference.service import MaintenanceTypeService
    q = (request.args.get("q") or "").strip().lower()
    rows = MaintenanceTypeService().list(include_inactive=True)
    if q:
        rows = [t for t in rows if q in " ".join(filter(None, [
            t.code, t.name, t.category, t.description])).lower()]
    from sqlalchemy import func
    from app.extensions import db
    from app.modules.maintenance_config.models import PMSchedule
    counts = dict(
        db.session.query(PMSchedule.maintenance_type_id, func.count(PMSchedule.id))
        .group_by(PMSchedule.maintenance_type_id)
        .all()
    )
    payload, err = _page([_mtype_json(t, counts.get(t.id, 0)) for t in rows])
    return err if err else jsonify(payload)


@bp.route("/maintenance-types/<int:type_id>", methods=["GET"])
@api_auth_required("maintenancetype.view")
def get_maintenance_type(api_user, type_id):
    from app.modules.master_data.reference.service import MaintenanceTypeService
    t = MaintenanceTypeService().get(type_id)
    if t is None:
        return _not_found("Maintenance type")
    return jsonify(_mtype_json(t))


@bp.route("/maintenance-types", methods=["POST"])
@api_auth_required("maintenancetype.create")
def create_maintenance_type(api_user):
    from app.modules.master_data.reference.service import MaintenanceTypeService
    from app.modules.master_data.org.service import DuplicateCodeError
    p = request.get_json(silent=True) or {}
    code = (p.get("code") or "").strip()
    name = (p.get("name") or "").strip()
    category = (p.get("category") or "").strip()
    if not code:
        return _validation("code is required.", "code")
    if not name:
        return _validation("name is required.", "name")
    if not category:
        return _validation("category is required.", "category")
    try:
        kind = (p.get("equipment_kind") or "").strip().upper() or None
        if kind not in (None, "TIRE", "BATTERY"):
            return _validation("equipment_kind must be TIRE, BATTERY or "
                               "empty.", "equipment_kind")
        t = MaintenanceTypeService().create(
            code=code, name=name, category=category,
            description=(p.get("description") or None),
            equipment_kind=kind)
    except DuplicateCodeError as e:
        return _validation(str(e), "code")
    return jsonify(_mtype_json(t)), 201


@bp.route("/maintenance-types/<int:type_id>", methods=["PUT", "PATCH"])
@api_auth_required("maintenancetype.update")
def update_maintenance_type(api_user, type_id):
    from app.modules.master_data.reference.service import MaintenanceTypeService
    p = request.get_json(silent=True) or {}
    fields = {}
    for k in ("name", "category", "description"):
        if k in p:
            fields[k] = (p[k].strip() if isinstance(p[k], str) else p[k]) or None
    if "equipment_kind" in p:
        kind = (p.get("equipment_kind") or "").strip().upper() or None
        if kind not in (None, "TIRE", "BATTERY"):
            return _validation("equipment_kind must be TIRE, BATTERY or "
                               "empty.", "equipment_kind")
        fields["equipment_kind"] = kind
    t = MaintenanceTypeService().update(type_id, **fields)
    if t is None:
        return _not_found("Maintenance type")
    return jsonify(_mtype_json(t))


@bp.route("/maintenance-types/<int:type_id>/deactivate", methods=["POST"])
@api_auth_required("maintenancetype.delete")
def deactivate_maintenance_type(api_user, type_id):
    from app.modules.master_data.reference.service import MaintenanceTypeService
    if MaintenanceTypeService().get(type_id) is None:
        return _not_found("Maintenance type")
    MaintenanceTypeService().deactivate(type_id)
    return jsonify({"ok": True})


# ── Registration Templates ──────────────────────────────────────────────────

def _regtmpl_json(t):
    match = "All Vehicles"
    if t.vehicle_brand:
        match = t.vehicle_brand.name
        if getattr(t, "vehicle_model_ref", None):
            match = f"{match} {t.vehicle_model_ref.name}"
    elif t.vehicle_type:
        match = t.vehicle_type.name
    return {
        "id": t.id,
        "match_label": match,
        "vehicle_type_id": t.vehicle_type_id,
        "vehicle_brand_id": t.vehicle_brand_id,
        "vehicle_model_id": t.vehicle_model_id,
        "interval_years": t.interval_years,
        "next_generation_policy": t.next_generation_policy,
        "priority": t.priority,
        "notify_before_days": t.notify_before_days,
        "is_active": bool(t.is_active),
        # Create/update already accepted this; nothing previously read
        # it back, so an edit form had no way to show -- or even know
        # about -- a template's existing checklist.
        "checklist_items": [
            {"id": i.id, "activity_code": i.activity_code,
             "activity_description": i.activity_description,
             "sort_order": i.sort_order}
            for i in t.checklist_items
        ],
    }


@bp.route("/registration-templates", methods=["GET"])
@api_auth_required("registrationtemplate.view")
def list_registration_templates(api_user):
    from app.modules.registration_config.service import RegistrationTemplateService
    q = (request.args.get("q") or "").strip().lower()
    rows = RegistrationTemplateService().list(include_inactive=True)
    items = []
    for t in rows:
        row = _regtmpl_json(t)
        if q and q not in " ".join(filter(None, [
            row["match_label"], str(row["interval_years"]),
            row["next_generation_policy"], row["priority"],
        ])).lower():
            continue
        items.append(row)
    payload, err = _page(items)
    return err if err else jsonify(payload)


@bp.route("/registration-templates/<int:tid>", methods=["GET"])
@api_auth_required("registrationtemplate.view")
def get_registration_template(api_user, tid):
    from app.modules.registration_config.service import RegistrationTemplateService
    t = RegistrationTemplateService().get_by_id(tid)
    if t is None:
        return _not_found("Registration template")
    return jsonify(_regtmpl_json(t))


@bp.route("/registration-templates", methods=["POST"])
@api_auth_required("registrationtemplate.create")
def create_registration_template(api_user):
    from app.modules.registration_config.service import RegistrationTemplateService
    p = request.get_json(silent=True) or {}
    def _int(k):
        v = p.get(k)
        if v in (None, ""):
            return None
        return int(v)
    try:
        t = RegistrationTemplateService().create(
            vehicle_type_id=_int("vehicle_type_id"),
            vehicle_brand_id=_int("vehicle_brand_id"),
            vehicle_model_id=_int("vehicle_model_id"),
            interval_years=int(p.get("interval_years") or 3),
            next_generation_policy=p.get("next_generation_policy") or "AUTO_SCHEDULE",
            notify_before_days=_int("notify_before_days"),
            priority=p.get("priority") or "MEDIUM",
            items=p.get("items") or [],
        )
    except Exception as e:
        return _validation(str(e))
    return jsonify(_regtmpl_json(t)), 201


@bp.route("/registration-templates/<int:tid>", methods=["PUT", "PATCH"])
@api_auth_required("registrationtemplate.update")
def update_registration_template(api_user, tid):
    from app.modules.registration_config.service import RegistrationTemplateService
    p = request.get_json(silent=True) or {}
    fields = {}
    for k in ("vehicle_type_id", "vehicle_brand_id", "vehicle_model_id",
              "interval_years", "next_generation_policy", "notify_before_days",
              "priority", "items"):
        if k in p:
            fields[k] = p[k]
    try:
        t = RegistrationTemplateService().update(tid, **fields)
    except Exception as e:
        return _validation(str(e))
    if t is None:
        return _not_found("Registration template")
    return jsonify(_regtmpl_json(t))


@bp.route("/registration-templates/<int:tid>/deactivate", methods=["POST"])
@api_auth_required("registrationtemplate.delete")
def deactivate_registration_template(api_user, tid):
    from app.modules.registration_config.service import RegistrationTemplateService
    if RegistrationTemplateService().get_by_id(tid) is None:
        return _not_found("Registration template")
    RegistrationTemplateService().deactivate(tid)
    return jsonify({"ok": True})


# ── MO Transaction Types (admin master) ─────────────────────────────────────

def _mott_json(t):
    dst = t.default_scope_template
    return {
        "id": t.id,
        "code": t.code,
        "name": t.name,
        "order_category": t.order_category,
        "group": t.group,
        "maintenance_class": getattr(t, "maintenance_class", None),
        "is_active": bool(t.is_active),
        "default_scope_template_id": t.default_scope_template_id,
        "default_scope_template_name": dst.name if dst else None,
    }


@bp.route("/mo-transaction-types/admin", methods=["GET"])
@api_auth_required("motransactiontype.view")
def list_mo_transaction_types_admin(api_user):
    """Admin list including inactive — distinct from dropdown endpoint."""
    from app.modules.transactions.maintenance_order.service import (
        TransactionTypeService)
    q = (request.args.get("q") or "").strip().lower()
    rows = TransactionTypeService().list(include_inactive=True)
    if q:
        rows = [t for t in rows if q in " ".join(filter(None, [
            t.code, t.name, t.order_category, t.group])).lower()]
    payload, err = _page([_mott_json(t) for t in rows])
    return err if err else jsonify(payload)


@bp.route("/mo-transaction-types/admin", methods=["POST"])
@api_auth_required("motransactiontype.create")
def create_mo_transaction_type(api_user):
    from app.modules.transactions.maintenance_order.service import (
        TransactionTypeService)
    p = request.get_json(silent=True) or {}
    code = (p.get("code") or "").strip()
    name = (p.get("name") or "").strip()
    cat = (p.get("order_category") or "").strip().upper()
    if not code or not name or cat not in ("MAINTENANCE", "OPERATIONAL"):
        return _validation(
            "code, name, and order_category (MAINTENANCE|OPERATIONAL) required.")
    t = TransactionTypeService().create(
        code=code, name=name, order_category=cat,
        group=(p.get("group") or None),
        sort_order=int(p.get("sort_order") or 0))
    return jsonify(_mott_json(t)), 201


@bp.route("/mo-transaction-types/admin/<int:tt_id>", methods=["PUT", "PATCH"])
@api_auth_required("motransactiontype.update")
def update_mo_transaction_type(api_user, tt_id):
    """Update editable fields on a transaction type.

    All fields optional -- send only what changed.
    default_scope_template_id:
      - an integer id  → set that template as the default
      - null / 0       → clear the default (no pre-fill)
      - absent from body → leave unchanged
    """
    from app.modules.transactions.maintenance_order.service import (
        TransactionTypeService, InvalidOrderCategoryError,
        InvalidOrderStateError)
    p = request.get_json(silent=True) or {}
    kwargs = {}
    for field in ("name", "order_category", "group", "maintenance_class"):
        if field in p:
            kwargs[field] = p[field]
    if "sort_order" in p:
        try:
            kwargs["sort_order"] = int(p["sort_order"])
        except (TypeError, ValueError):
            return _validation("sort_order must be an integer.", "sort_order")
    if "default_scope_template_id" in p:
        raw = p["default_scope_template_id"]
        try:
            kwargs["default_scope_template_id"] = (
                int(raw) if raw not in (None, "", 0, "0") else None)
        except (TypeError, ValueError):
            return _validation("default_scope_template_id must be an integer "
                               "or null.", "default_scope_template_id")
    try:
        tt = TransactionTypeService().update(tt_id, **kwargs)
    except InvalidOrderStateError as e:
        return _not_found() if "not found" in str(e).lower() else _conflict(str(e))
    except InvalidOrderCategoryError as e:
        return _validation(str(e), "order_category")
    return jsonify(_mott_json(tt))


@bp.route("/mo-transaction-types/admin/<int:tt_id>/deactivate", methods=["POST"])
@api_auth_required("motransactiontype.delete")
def deactivate_mo_transaction_type(api_user, tt_id):
    from app.modules.transactions.maintenance_order.service import (
        TransactionTypeService)
    TransactionTypeService().deactivate(tt_id)
    return jsonify({"ok": True})


@bp.route("/mo-transaction-types/admin/<int:tt_id>/reactivate", methods=["POST"])
@api_auth_required("motransactiontype.create")
def reactivate_mo_transaction_type(api_user, tt_id):
    from app.modules.transactions.maintenance_order.service import (
        TransactionTypeService)
    TransactionTypeService().reactivate(tt_id)
    return jsonify({"ok": True})


# ── Regions (Stage 3A — D3: configurable master, not free text) ────────────

def _region_json(r):
    return {
        "id": r.id,
        "code": r.code,
        "name": r.name,
        "description": r.description,
        "is_active": bool(r.is_active),
    }


@bp.route("/regions", methods=["GET"])
@api_auth_required("region.view")
def list_regions(api_user):
    from app.modules.master_data.org.service import RegionService
    q = (request.args.get("q") or "").strip().lower()
    rows = RegionService().list(include_inactive=True)
    if q:
        rows = [r for r in rows
               if q in " ".join(filter(None, [r.code, r.name])).lower()]
    payload, err = _page([_region_json(r) for r in rows])
    return err if err else jsonify(payload)


@bp.route("/regions/<int:region_id>", methods=["GET"])
@api_auth_required("region.view")
def get_region(api_user, region_id):
    from app.modules.master_data.org.service import RegionService
    r = RegionService().get(region_id)
    if r is None:
        return _not_found("Region")
    return jsonify(_region_json(r))


@bp.route("/regions", methods=["POST"])
@api_auth_required("region.create")
def create_region(api_user):
    from app.modules.master_data.org.service import (
        RegionService, DuplicateCodeError)
    p = request.get_json(silent=True) or {}
    code = (p.get("code") or "").strip()
    name = (p.get("name") or "").strip()
    if not code:
        return _validation("code is required.", "code")
    if not name:
        return _validation("name is required.", "name")
    try:
        r = RegionService().create(
            code=code, name=name, description=(p.get("description") or None))
    except DuplicateCodeError as e:
        return _validation(str(e), "code")
    return jsonify(_region_json(r)), 201


@bp.route("/regions/<int:region_id>", methods=["PUT", "PATCH"])
@api_auth_required("region.update")
def update_region(api_user, region_id):
    from app.modules.master_data.org.service import RegionService
    p = request.get_json(silent=True) or {}
    fields = {}
    for k in ("code", "name", "description"):
        if k in p:
            fields[k] = (p[k].strip() if isinstance(p[k], str) else p[k]) or None
    r = RegionService().update(region_id, **fields)
    if r is None:
        return _not_found("Region")
    return jsonify(_region_json(r))


@bp.route("/regions/<int:region_id>/deactivate", methods=["POST"])
@api_auth_required("region.delete")
def deactivate_region(api_user, region_id):
    from app.modules.master_data.org.service import RegionService
    if RegionService().get(region_id) is None:
        return _not_found("Region")
    RegionService().deactivate(region_id)
    return jsonify({"ok": True})


# ── Area / District (Stage 3A — belongs to a Region) ────────────────────────

def _area_district_json(a):
    return {
        "id": a.id,
        "code": a.code,
        "name": a.name,
        "region_id": a.region_id,
        "region": a.region.name if a.region else None,
        "description": a.description,
        "is_active": bool(a.is_active),
    }


@bp.route("/area-districts", methods=["GET"])
@api_auth_required("areadistrict.view")
def list_area_districts(api_user):
    from app.modules.master_data.org.service import AreaDistrictService
    region_id = request.args.get("region_id", type=int)
    if region_id:
        rows = AreaDistrictService().list_for_region(
            region_id, include_inactive=True)
    else:
        rows = AreaDistrictService().list(include_inactive=True)
    q = (request.args.get("q") or "").strip().lower()
    if q:
        rows = [a for a in rows
               if q in " ".join(filter(None, [a.code, a.name])).lower()]
    payload, err = _page([_area_district_json(a) for a in rows])
    return err if err else jsonify(payload)


@bp.route("/area-districts/<int:area_id>", methods=["GET"])
@api_auth_required("areadistrict.view")
def get_area_district(api_user, area_id):
    from app.modules.master_data.org.service import AreaDistrictService
    a = AreaDistrictService().get(area_id)
    if a is None:
        return _not_found("Area/District")
    return jsonify(_area_district_json(a))


@bp.route("/area-districts", methods=["POST"])
@api_auth_required("areadistrict.create")
def create_area_district(api_user):
    from app.modules.master_data.org.service import (
        AreaDistrictService, DuplicateCodeError, InvalidOrgDataError)
    p = request.get_json(silent=True) or {}
    code = (p.get("code") or "").strip()
    name = (p.get("name") or "").strip()
    region_id = p.get("region_id")
    if not code:
        return _validation("code is required.", "code")
    if not name:
        return _validation("name is required.", "name")
    if not region_id:
        return _validation("region_id is required.", "region_id")
    try:
        a = AreaDistrictService().create(
            code=code, name=name, region_id=int(region_id),
            description=(p.get("description") or None))
    except DuplicateCodeError as e:
        return _validation(str(e), "code")
    except InvalidOrgDataError as e:
        return _validation(str(e), "region_id")
    return jsonify(_area_district_json(a)), 201


@bp.route("/area-districts/<int:area_id>", methods=["PUT", "PATCH"])
@api_auth_required("areadistrict.update")
def update_area_district(api_user, area_id):
    from app.modules.master_data.org.service import AreaDistrictService
    p = request.get_json(silent=True) or {}
    fields = {}
    for k in ("code", "name", "description"):
        if k in p:
            fields[k] = (p[k].strip() if isinstance(p[k], str) else p[k]) or None
    if "region_id" in p and p["region_id"]:
        fields["region_id"] = int(p["region_id"])
    a = AreaDistrictService().update(area_id, **fields)
    if a is None:
        return _not_found("Area/District")
    return jsonify(_area_district_json(a))


@bp.route("/area-districts/<int:area_id>/deactivate", methods=["POST"])
@api_auth_required("areadistrict.delete")
def deactivate_area_district(api_user, area_id):
    from app.modules.master_data.org.service import AreaDistrictService
    if AreaDistrictService().get(area_id) is None:
        return _not_found("Area/District")
    AreaDistrictService().deactivate(area_id)
    return jsonify({"ok": True})


# ── Companies (Stage 3A — D2: short_name may repeat under a different ──────
#    Region + Area/District; D5: vendor eligibility and MO vendor rules
#    read a vehicle's company to resolve its region)

def _org_company_json(c):
    return {
        "id": c.id,
        "short_name": c.short_name,
        "company_name": c.company_name,
        "region_id": c.region_id,
        "region": c.region.name if c.region else None,
        "area_district_id": c.area_district_id,
        "area_district": c.area_district.name if c.area_district else None,
        "legacy_branch_id": c.legacy_branch_id,
        "is_active": bool(c.is_active),
    }


@bp.route("/org-companies", methods=["GET"])
@api_auth_required("orgcompany.view")
def list_org_companies(api_user):
    from app.modules.master_data.org.service import OrgCompanyService
    rows = OrgCompanyService().list(include_inactive=True)
    region_id = request.args.get("region_id", type=int)
    area_district_id = request.args.get("area_district_id", type=int)
    if region_id:
        rows = [c for c in rows if c.region_id == region_id]
    if area_district_id:
        rows = [c for c in rows if c.area_district_id == area_district_id]
    q = (request.args.get("q") or "").strip().lower()
    if q:
        rows = [c for c in rows if q in " ".join(filter(
            None, [c.short_name, c.company_name])).lower()]
    payload, err = _page([_org_company_json(c) for c in rows])
    return err if err else jsonify(payload)


@bp.route("/org-companies/<int:company_id>", methods=["GET"])
@api_auth_required("orgcompany.view")
def get_org_company(api_user, company_id):
    from app.modules.master_data.org.service import OrgCompanyService
    c = OrgCompanyService().get(company_id)
    if c is None:
        return _not_found("Company")
    return jsonify(_org_company_json(c))


@bp.route("/org-companies", methods=["POST"])
@api_auth_required("orgcompany.create")
def create_org_company(api_user):
    from app.modules.master_data.org.service import (
        OrgCompanyService, DuplicateCodeError, InvalidOrgDataError)
    p = request.get_json(silent=True) or {}
    short_name = (p.get("short_name") or "").strip()
    if not short_name:
        return _validation("short_name is required.", "short_name")
    try:
        c = OrgCompanyService().create(
            short_name=short_name,
            # The browser form sends every field as a JSON string (e.g.
            # "7"), never a number. Coerce to int here -- same as
            # update_org_company below -- so the service layer's int
            # comparisons against DB-loaded ids don't spuriously fail.
            region_id=(int(p["region_id"]) if p.get("region_id") else None),
            area_district_id=(int(p["area_district_id"])
                               if p.get("area_district_id") else None),
            company_name=(p.get("company_name") or None),
            legacy_branch_id=(int(p["legacy_branch_id"])
                               if p.get("legacy_branch_id") else None))
    except DuplicateCodeError as e:
        return _validation(str(e), "short_name")
    except InvalidOrgDataError as e:
        return _validation(str(e), "region_id")
    return jsonify(_org_company_json(c)), 201


@bp.route("/org-companies/<int:company_id>", methods=["PUT", "PATCH"])
@api_auth_required("orgcompany.update")
def update_org_company(api_user, company_id):
    from app.modules.master_data.org.service import OrgCompanyService
    p = request.get_json(silent=True) or {}
    fields = {}
    for k in ("short_name", "company_name"):
        if k in p:
            fields[k] = (p[k].strip() if isinstance(p[k], str) else p[k]) or None
    for k in ("region_id", "area_district_id", "legacy_branch_id"):
        if k in p:
            fields[k] = (int(p[k]) if p[k] else None)
    c = OrgCompanyService().update(company_id, **fields)
    if c is None:
        return _not_found("Company")
    return jsonify(_org_company_json(c))


@bp.route("/org-companies/<int:company_id>/deactivate", methods=["POST"])
@api_auth_required("orgcompany.delete")
def deactivate_org_company(api_user, company_id):
    from app.modules.master_data.org.service import OrgCompanyService
    if OrgCompanyService().get(company_id) is None:
        return _not_found("Company")
    OrgCompanyService().deactivate(company_id)
    return jsonify({"ok": True})


@bp.route("/org-companies/migrate-branches", methods=["POST"])
@api_auth_required("orgcompany.create")
def migrate_branches_to_companies(api_user):
    """Stage 3B (D6): upload a Branch -> Company mapping sheet. Dry-run
    by default (preview only, nothing written); pass commit=1 to apply.
    Same upload convention as /fuel/import."""
    from app.modules.master_data.org.company_migration import (
        import_company_mapping)
    uploaded = request.files.get("file")
    if uploaded is None or not uploaded.filename:
        return jsonify({"error": "validation",
                        "message": "Choose a file to upload."}), 400
    commit = request.form.get("commit") == "1"
    try:
        stats = import_company_mapping(uploaded.stream, dry_run=not commit)
    except ValueError as exc:
        return jsonify({"error": "validation", "message": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": "validation",
                        "message": f"Could not read the file: {exc}"}), 400
    return jsonify(stats)
