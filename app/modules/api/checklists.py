"""Vehicle Checklist API."""
from datetime import date

from flask import jsonify, request

from app.modules.api.auth import api_auth_required
from app.modules.api.coercion import Coercer, FieldValueError
from app.modules.api.routes import bp
from app.modules.transactions.vehicle_checklist.service import (
    VehicleChecklistService, ChecklistError,
)

_COERCER = Coercer(
    ints={"vehicle_id": "Vehicle", "driver_id": "Driver",
          "template_id": "Template", "odometer": "Odometer",
          "page": "Page", "per_page": "Per page", "branch_id": "Branch"},
    dates={"inspection_date": "Inspection Date",
           "date_from": "From", "date_to": "To"},
)


def _bad(message, field=None):
    return jsonify({"error": "validation", "message": message,
                    "fields": {field or "_": message}}), 400


def _conflict(message):
    return jsonify({"error": "conflict", "message": message}), 409


def _row(cl, detail=False):
    v = cl.vehicle
    d = {
        "id": cl.id,
        "document_number": cl.document_number,
        "status": cl.status,
        "result": cl.result,
        "score": str(cl.score) if cl.score is not None else None,
        "inspection_date": cl.inspection_date.isoformat() if cl.inspection_date else None,
        "inspection_time": cl.inspection_time.strftime("%H:%M") if cl.inspection_time else None,
        "odometer": cl.odometer,
        "vehicle_id": cl.vehicle_id,
        "plate_number": v.plate_number if v else None,
        "conduction_number": v.conduction_number if v else None,
        "vehicle_label": " ".join(filter(None, [
            getattr(v, "brand", None), getattr(v, "model", None),
            f"({v.year})" if v and v.year else None,
        ])) if v else None,
        "driver": cl.driver.full_name if cl.driver else None,
        "driver_id": cl.driver_id,
        "branch": cl.branch.name if cl.branch else None,
        "branch_id": cl.branch_id,
        "template_id": cl.template_id,
        "template_name": cl.template.name if cl.template else None,
        "pass_count": cl.pass_count,
        "attention_count": cl.attention_count,
        "fail_count": cl.fail_count,
        "applicable_count": cl.applicable_count,
        "submitted_at": cl.submitted_at.isoformat() if cl.submitted_at else None,
        "remarks": cl.remarks,
        "defect_count": len(cl.defects or []),
    }
    if not detail:
        return d
    groups = {}
    for ln in sorted(cl.lines, key=lambda x: x.sort_order or 0):
        groups.setdefault(ln.category_name, []).append({
            "id": ln.id,
            "item_id": ln.item_id,
            "item_name": ln.item_name,
            "response": ln.response,
            "is_required": ln.is_required,
            "is_safety": ln.is_safety,
        })
    d["groups"] = [{"category": k, "items": v} for k, v in groups.items()]
    d["defects"] = [{
        "id": df.id,
        "line_id": df.line_id,
        "item_name": df.item_name,
        "observation": df.observation,
        "severity": df.severity,
        "create_maintenance": df.create_maintenance,
        "maintenance_order_id": df.maintenance_order_id,
        "maintenance_order": (
            df.maintenance_order.document_number
            if df.maintenance_order else None),
        "status": df.status,
    } for df in cl.defects]
    if v:
        d["vehicle"] = {
            "id": v.id,
            "plate_number": v.plate_number,
            "conduction_number": v.conduction_number,
            "brand": v.brand,
            "model": v.model,
            "year": v.year,
            "vehicle_type": v.vehicle_type.name if v.vehicle_type else None,
            "current_odometer": v.current_odometer,
            "assigned_driver": v.assigned_driver.full_name if v.assigned_driver else None,
            "branch": v.branch.name if v.branch else None,
        }
    return d


def _tmpl(t):
    cats = []
    items_n = 0
    for c in t.categories:
        its = [{"id": i.id, "name": i.name, "required": i.required,
                "is_safety": i.is_safety, "sort_order": i.sort_order}
               for i in c.items]
        items_n += len(its)
        cats.append({"id": c.id, "name": c.name, "sort_order": c.sort_order,
                     "items": its})
    return {
        "id": t.id, "name": t.name, "description": t.description,
        "status": t.status, "vehicle_type_id": t.vehicle_type_id,
        "category_count": len(cats), "item_count": items_n,
        "categories": cats,
    }


@bp.route("/checklists/dashboard", methods=["GET"])
@api_auth_required("checklist.view")
def checklist_dashboard(api_user):
    data = VehicleChecklistService().dashboard()
    data["recent"] = [_row(r) for r in data["recent"]]
    return jsonify(data)


@bp.route("/checklists/defects/summary", methods=["GET"])
@api_auth_required("checklist.report")
def checklist_defect_summary(api_user):
    return jsonify(VehicleChecklistService().defect_summary())


@bp.route("/checklists/templates", methods=["GET"])
@api_auth_required("checklist.view")
def checklist_templates(api_user):
    rows = VehicleChecklistService().list_templates()
    return jsonify({"items": [_tmpl(t) for t in rows]})


@bp.route("/checklists/templates", methods=["POST"])
@api_auth_required("checklist.manage")
def checklist_template_create(api_user):
    p = request.get_json(silent=True) or {}
    if not p.get("name"):
        return _bad("Template name is required.", "name")
    t = VehicleChecklistService().create_template(
        name=p["name"], description=p.get("description"),
        vehicle_type_id=p.get("vehicle_type_id"))
    return jsonify(_tmpl(t)), 201


@bp.route("/checklists/templates/<int:tid>", methods=["GET"])
@api_auth_required("checklist.view")
def checklist_template_detail(api_user, tid):
    t = VehicleChecklistService().get_template(tid)
    if t is None:
        return jsonify({"error": "not_found", "message": "Template not found."}), 404
    return jsonify(_tmpl(t))


@bp.route("/checklists/templates/<int:tid>/categories", methods=["POST"])
@api_auth_required("checklist.manage")
def checklist_add_category(api_user, tid):
    p = request.get_json(silent=True) or {}
    if not p.get("name"):
        return _bad("Category name is required.", "name")
    c = VehicleChecklistService().add_category(
        tid, p["name"], sort_order=int(p.get("sort_order") or 0))
    return jsonify({"id": c.id, "name": c.name}), 201


@bp.route("/checklists/categories/<int:cid>/items", methods=["POST"])
@api_auth_required("checklist.manage")
def checklist_add_item(api_user, cid):
    p = request.get_json(silent=True) or {}
    if not p.get("name"):
        return _bad("Item name is required.", "name")
    it = VehicleChecklistService().add_item(
        cid, name=p["name"], required=bool(p.get("required", True)),
        is_safety=bool(p.get("is_safety")),
        photo_required_on_fail=bool(p.get("photo_required_on_fail")),
        maintenance_allowed=bool(p.get("maintenance_allowed", True)),
        sort_order=int(p.get("sort_order") or 0))
    return jsonify({"id": it.id, "name": it.name}), 201


@bp.route("/checklists", methods=["GET"])
@api_auth_required("checklist.view")
def checklist_list(api_user):
    try:
        c = _COERCER.fields(request.args, [
            "vehicle_id", "driver_id", "template_id", "odometer",
            "page", "per_page", "branch_id", "inspection_date",
            "date_from", "date_to"])
    except FieldValueError as e:
        return _bad(str(e), e.field)
    page = int(c.get("page") or 1)
    per_page = min(int(c.get("per_page") or 25), 100)
    rows, total = VehicleChecklistService().list_checklists(
        branch_id=c.get("branch_id"), vehicle_id=c.get("vehicle_id"),
        result=request.args.get("result") or None,
        status=request.args.get("status") or None,
        date_from=c.get("date_from"), date_to=c.get("date_to"),
        user=api_user, page=page, per_page=per_page)
    return jsonify({"items": [_row(r) for r in rows], "total": total,
                    "page": page, "per_page": per_page})


@bp.route("/checklists", methods=["POST"])
@api_auth_required("checklist.create")
def checklist_create(api_user):
    p = request.get_json(silent=True) or {}
    try:
        c = _COERCER.fields(p, [
            "vehicle_id", "driver_id", "template_id", "odometer",
            "inspection_date"])
    except FieldValueError as e:
        return _bad(str(e), e.field)
    if not c.get("vehicle_id"):
        return _bad("Vehicle is required.", "vehicle_id")
    if not c.get("template_id"):
        return _bad("Template is required.", "template_id")

    # A driver may only raise a checklist against a vehicle assigned to
    # them.
    #
    # Found on a real device: the New Vehicle Checklist picker listed
    # the whole branch for a driver holding one vehicle. That is finding
    # J1 surfacing in a screen built before the /my/* namespace existed
    # -- the picker calls /reference/vehicles, which scopes on ORG.
    #
    # Narrowing the picker alone would not have fixed it. A picker is a
    # convenience; THIS is the control, and it is what a request built
    # by hand hits.
    #
    # checklist.submit is the reviewer signal, exactly as it is for list
    # and detail visibility: a Fleet Officer legitimately raises
    # checklists against any vehicle in scope, a driver does not.
    if not api_user.has_permission("checklist.submit"):
        from app.modules.user_management.assignee_scope_service import (
            AssigneeScopeService)
        if not AssigneeScopeService().covers_vehicle(
                api_user, c["vehicle_id"]):
            return jsonify({
                "error": "forbidden",
                "message": ("That vehicle is not assigned to you. You can "
                            "only start a checklist for your own vehicle."),
            }), 403

    try:
        cl = VehicleChecklistService().create(
            vehicle_id=c["vehicle_id"], template_id=c["template_id"],
            user=api_user, driver_id=c.get("driver_id"),
            odometer=c.get("odometer"),
            inspection_date=c.get("inspection_date"),
            inspection_time=p.get("inspection_time"),
            remarks=p.get("remarks"))
    except ChecklistError as e:
        return _conflict(str(e))
    return jsonify(_row(cl, detail=True)), 201


@bp.route("/checklists/<int:cid>", methods=["GET"])
@api_auth_required("checklist.view")
def checklist_detail(api_user, cid):
    cl = VehicleChecklistService().get_visible(cid, api_user)
    if cl is None:
        return jsonify({"error": "not_found", "message": "Checklist not found."}), 404
    return jsonify(_row(cl, detail=True))


@bp.route("/checklists/<int:cid>", methods=["PUT"])
@api_auth_required("checklist.update")
def checklist_save(api_user, cid):
    # Scope checked BEFORE the write. Reading is not the only risk here:
    # without this, another driver's inspection could be edited by id
    # even once it had become invisible in the list.
    if VehicleChecklistService().get_visible(cid, api_user) is None:
        return jsonify({"error": "not_found",
                        "message": "Checklist not found."}), 404
    p = request.get_json(silent=True) or {}
    try:
        cl = VehicleChecklistService().save_draft(
            cid,
            responses=p.get("responses") or [],
            defects=p.get("defects") or [],
            odometer=p.get("odometer"),
            driver_id=p.get("driver_id"),
            remarks=p.get("remarks"))
    except ChecklistError as e:
        return _conflict(str(e))
    return jsonify(_row(cl, detail=True))


@bp.route("/checklists/<int:cid>/submit", methods=["POST"])
@api_auth_required("checklist.submit")
def checklist_submit(api_user, cid):
    try:
        cl, mos = VehicleChecklistService().submit(cid, api_user)
    except ChecklistError as e:
        return _conflict(str(e))
    data = _row(cl, detail=True)
    data["maintenance_created"] = mos
    return jsonify(data)
