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
    return {
        "id": b.id,
        "code": b.code,
        "name": b.name,
        "address": b.address,
        "city": b.city,
        "phone": b.phone,
        "email": b.email,
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
        BranchService, DuplicateCodeError)
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
            email=(p.get("email") or None))
    except DuplicateCodeError as e:
        return _validation(str(e), "code")
    return jsonify(_branch_json(b)), 201


@bp.route("/branches/<int:branch_id>", methods=["PUT", "PATCH"])
@api_auth_required("branch.update")
def update_branch(api_user, branch_id):
    from app.modules.master_data.org.service import BranchService
    p = request.get_json(silent=True) or {}
    fields = {}
    for k in ("code", "name", "address", "city", "phone", "email"):
        if k in p:
            fields[k] = (p[k].strip() if isinstance(p[k], str) else p[k]) or None
    b = BranchService().update(branch_id, **fields)
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

def _brand_json(b):
    return {
        "id": b.id,
        "name": b.name,
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
    payload, err = _page([_brand_json(b) for b in rows])
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
