"""Authority To Drive (ATD) API for React."""
from datetime import date

from flask import jsonify, request

from app.modules.api.auth import api_auth_required
from app.modules.api.routes import bp


def _bad(message, code=400):
    return jsonify({"error": "bad_request", "message": message}), code


def _not_found(label="ATD"):
    return jsonify({"error": "not_found", "message": f"{label} not found."}), 404


def _validation(message, field="_"):
    return jsonify({"error": "validation", "message": message,
                    "fields": {field: message}}), 400


def _conflict(message):
    return jsonify({"error": "conflict", "message": message}), 409


def _iso(d):
    if d is None:
        return None
    if hasattr(d, "isoformat"):
        return d.isoformat()
    return str(d)


def _atd_json(a, *, detail=False):
    plate = None
    vehicle_label = None
    if a.vehicle:
        plate = a.vehicle.plate_number or a.vehicle.conduction_number
        vehicle_label = f"{a.vehicle.brand or ''} {a.vehicle.model or ''}".strip()
    driver_name = None
    if a.driver:
        driver_name = getattr(a.driver, "full_name", None) or getattr(
            a.driver, "name", None)
    data = {
        "id": a.id,
        "document_number": a.document_number,
        "status": a.status,
        "vehicle_id": a.vehicle_id,
        "plate_number": plate,
        "vehicle_label": vehicle_label,
        "driver_id": a.driver_id,
        "driver_name": driver_name,
        "purpose": a.purpose,
        "valid_from": _iso(a.valid_from),
        "valid_to": _iso(a.valid_to),
        "odometer_out": a.odometer_out,
        "odometer_in": a.odometer_in,
        "maintenance_order_id": a.maintenance_order_id,
        "created_at": _iso(getattr(a, "created_at", None)),
    }
    if detail:
        mo = a.maintenance_order
        data["maintenance_order_number"] = (
            mo.document_number if mo else None)
        data["approval_status"] = (
            a.approval_instance.status if a.approval_instance else None)
    return data



def _atd_print_extras(a, data, api_user=None):
    """Enrich detail/print payload with driver, vehicle, requester, full
    approval line (same shape as Jinja approval_chain helper)."""
    from app.core.approval.engine import ApprovalEngine

    driver = a.driver
    data["driver_department"] = (
        driver.department.name
        if driver is not None and getattr(driver, "department", None)
        else None)
    data["driver_employee_number"] = (
        getattr(driver, "employee_number", None) if driver else None)
    data["driver_license_number"] = (
        getattr(driver, "license_number", None) if driver else None)
    vehicle = a.vehicle
    data["vehicle_brand"] = getattr(vehicle, "brand", None) if vehicle else None
    data["vehicle_model"] = getattr(vehicle, "model", None) if vehicle else None

    requester = getattr(a, "requester", None)
    data["requester_name"] = (
        getattr(requester, "full_name", None)
        or getattr(requester, "username", None)
        if requester else None)
    data["requester_branch"] = (
        requester.branch.name
        if requester is not None and getattr(requester, "branch", None)
        else None)

    inst = getattr(a, "approval_instance", None)
    engine = ApprovalEngine()
    chain = []
    if inst is not None:
        for entry in engine.get_approval_chain(inst):
            acted_at = entry.get("acted_at")
            chain.append({
                "level_number": entry.get("level_number"),
                "approver_label": entry.get("approver_label"),
                "status": entry.get("status"),  # APPROVED|REJECTED|RETURNED|CURRENT|WAITING
                "acted_by_name": entry.get("acted_by_name"),
                "acted_at": acted_at.isoformat() if acted_at is not None else None,
                "remarks": entry.get("remarks"),
            })
        data["approval_instance_status"] = inst.status
        data["approval_current_level"] = inst.current_level
        data["can_act"] = bool(
            api_user and engine.is_eligible_approver(inst, api_user))
    else:
        data["approval_instance_status"] = None
        data["approval_current_level"] = None
        data["can_act"] = False
    data["approval_chain"] = chain
    return data

@bp.route("/atd", methods=["GET"])
@api_auth_required("atd.view")
def list_atd(api_user):
    from app.modules.transactions.atd.service import ATDService
    try:
        page = int(request.args.get("page", 1))
        page_size = int(request.args.get("page_size", 25))
    except (TypeError, ValueError):
        return _bad("page and page_size must be integers.")
    page = max(1, page)
    page_size = max(1, min(page_size, 100))
    q = (request.args.get("q") or "").strip() or None
    status = (request.args.get("status") or "").strip().upper() or None
    svc = ATDService()
    if hasattr(svc, "list_filtered"):
        rows, pagination = svc.list_filtered(
            user=api_user, page=page, per_page=page_size,
            search=q, status=status)
        items = [_atd_json(a) for a in rows]
        return jsonify({
            "items": items,
            "total": pagination.total,
            "page": page,
            "page_size": page_size,
            "pages": max(1, pagination.pages or 1),
        })
    rows = svc.list(user=api_user)
    if status:
        rows = [a for a in rows if a.status == status]
    total = len(rows)
    start = (page - 1) * page_size
    items = [_atd_json(a) for a in rows[start:start + page_size]]
    return jsonify({
        "items": items, "total": total, "page": page,
        "page_size": page_size,
        "pages": max(1, (total + page_size - 1) // page_size),
    })


@bp.route("/atd/<int:aid>", methods=["GET"])
@api_auth_required("atd.view")
def get_atd(api_user, aid):
    from app.modules.transactions.atd.service import ATDService
    a = ATDService().get_visible(aid, api_user)
    if a is None:
        return _not_found()
    data = _atd_json(a, detail=True)
    return jsonify(_atd_print_extras(a, data, api_user=api_user))


@bp.route("/atd", methods=["POST"])
@api_auth_required("atd.create")
def create_atd(api_user):
    from app.modules.transactions.atd.service import ATDService
    p = request.get_json(silent=True) or {}
    try:
        vehicle_id = int(p["vehicle_id"])
        driver_id = int(p["driver_id"])
    except (KeyError, TypeError, ValueError):
        return _validation("vehicle_id and driver_id are required.", "vehicle_id")
    purpose = (p.get("purpose") or "").strip()
    if not purpose:
        return _validation("purpose is required.", "purpose")
    try:
        valid_from = date.fromisoformat(str(p["valid_from"])[:10])
        valid_to = date.fromisoformat(str(p["valid_to"])[:10])
    except (KeyError, TypeError, ValueError):
        return _validation("valid_from and valid_to (YYYY-MM-DD) are required.",
                           "valid_from")
    if valid_to < valid_from:
        return _validation("valid_to must be on or after valid_from.", "valid_to")
    mo_id = p.get("maintenance_order_id")
    try:
        mo_id = int(mo_id) if mo_id not in (None, "") else None
    except (TypeError, ValueError):
        return _validation("maintenance_order_id must be an integer.",
                           "maintenance_order_id")
    odo = p.get("odometer_out")
    try:
        odo = int(odo) if odo not in (None, "") else None
    except (TypeError, ValueError):
        return _validation("odometer_out must be an integer.", "odometer_out")
    a = ATDService().create(
        vehicle_id=vehicle_id, driver_id=driver_id, purpose=purpose,
        valid_from=valid_from, valid_to=valid_to, user=api_user,
        maintenance_order_id=mo_id, odometer_out=odo)
    return jsonify(_atd_json(a, detail=True)), 201


@bp.route("/atd/<int:aid>/submit", methods=["POST"])
@api_auth_required("atd.update")
def submit_atd(api_user, aid):
    from app.modules.transactions.atd.service import ATDService
    try:
        ATDService().submit(aid, api_user)
    except Exception as e:
        return _conflict(str(e))
    return jsonify({"ok": True})


@bp.route("/atd/<int:aid>/activate", methods=["POST"])
@api_auth_required("atd.update")
def activate_atd(api_user, aid):
    from app.modules.transactions.atd.service import (
        ATDService, InvalidATDStateError)
    try:
        ATDService().activate(aid)
    except InvalidATDStateError as e:
        return _conflict(str(e))
    except Exception as e:
        return _conflict(str(e))
    return jsonify({"ok": True})


@bp.route("/atd/<int:aid>/cancel", methods=["POST"])
@api_auth_required("atd.update")
def cancel_atd(api_user, aid):
    from app.modules.transactions.atd.service import ATDService
    p = request.get_json(silent=True) or {}
    try:
        ATDService().cancel(aid, api_user, remarks=p.get("remarks"))
    except Exception as e:
        return _conflict(str(e))
    return jsonify({"ok": True})


@bp.route("/atd/<int:aid>/odometer-in", methods=["POST"])
@api_auth_required("atd.update")
def record_atd_odometer_in(api_user, aid):
    from app.modules.transactions.atd.service import ATDService
    p = request.get_json(silent=True) or {}
    try:
        odo = int(p["odometer_in"])
    except (KeyError, TypeError, ValueError):
        return _validation("odometer_in is required.", "odometer_in")
    try:
        ATDService().record_odometer_in(aid, odometer_in=odo)
    except Exception as e:
        return _conflict(str(e))
    return jsonify({"ok": True})


@bp.route("/atd/<int:aid>/print", methods=["GET"])
@api_auth_required("atd.print")
def atd_print(api_user, aid):
    """Print payload matching Jinja atd_print.html.

    Two-copy gate-guard authorization: company letterhead, body text,
    vehicle table, requester + approved approval levels, print stamp.
    """
    from datetime import datetime
    from app.modules.transactions.atd.service import ATDService
    from app.modules.api.company_letterhead import company_letterhead

    a = ATDService().get_visible(aid, api_user)
    if a is None:
        return _not_found()

    data = _atd_json(a, detail=True)

    # Extra print fields used by the official slip
    driver = a.driver
    data["driver_department"] = (
        driver.department.name
        if driver is not None and getattr(driver, "department", None)
        else None)
    data["driver_employee_number"] = getattr(driver, "employee_number", None) if driver else None
    data["driver_license_number"] = getattr(driver, "license_number", None) if driver else None
    vehicle = a.vehicle
    data["vehicle_brand"] = getattr(vehicle, "brand", None) if vehicle else None
    data["vehicle_model"] = getattr(vehicle, "model", None) if vehicle else None

    requester = getattr(a, "requester", None)
    data["requester_name"] = (
        getattr(requester, "full_name", None) or getattr(requester, "username", None)
        if requester else None)

    chain = []
    inst = getattr(a, "approval_instance", None)
    if inst is not None:
        levels = getattr(inst, "levels", None) or getattr(inst, "level_actions", None) or []
        for lvl in levels:
            status = getattr(lvl, "status", None)
            if status != "APPROVED":
                continue
            chain.append({
                "status": status,
                "acted_by_name": (
                    getattr(lvl, "acted_by_name", None)
                    or getattr(getattr(lvl, "acted_by", None), "full_name", None)
                    or getattr(getattr(lvl, "acted_by", None), "username", None)
                ),
            })
    data["approval_chain"] = chain

    return jsonify({
        "atd": data,
        # Was reading `address_line`, a column CompanyProfile does not
        # have (the real one is address_line1) -- so this printed
        # letterhead's address line had always been blank. Switched to
        # the shared helper so the field names cannot drift again.
        "company": company_letterhead(),
        "generated_at": datetime.now().isoformat(),
        "copies": 2,
    })


@bp.route("/atd/<int:aid>/approve", methods=["POST"])
@api_auth_required("atd.view")  # eligibility enforced by engine
def approve_atd(api_user, aid):
    from app.modules.transactions.atd.service import ATDService
    p = request.get_json(silent=True) or {}
    try:
        ATDService().approve(aid, api_user, remarks=p.get("remarks"))
    except Exception as e:
        return _conflict(str(e))
    a = ATDService().get_visible(aid, api_user)
    data = _atd_json(a, detail=True)
    return jsonify(_atd_print_extras(a, data, api_user=api_user))


@bp.route("/atd/<int:aid>/reject", methods=["POST"])
@api_auth_required("atd.view")
def reject_atd(api_user, aid):
    from app.modules.transactions.atd.service import ATDService
    p = request.get_json(silent=True) or {}
    try:
        ATDService().reject(aid, api_user, remarks=p.get("remarks"))
    except Exception as e:
        return _conflict(str(e))
    a = ATDService().get_visible(aid, api_user)
    data = _atd_json(a, detail=True)
    return jsonify(_atd_print_extras(a, data, api_user=api_user))


@bp.route("/atd/<int:aid>/return", methods=["POST"])
@api_auth_required("atd.view")
def return_atd(api_user, aid):
    from app.modules.transactions.atd.service import ATDService
    p = request.get_json(silent=True) or {}
    try:
        ATDService().return_document(aid, api_user, remarks=p.get("remarks"))
    except Exception as e:
        return _conflict(str(e))
    a = ATDService().get_visible(aid, api_user)
    data = _atd_json(a, detail=True)
    return jsonify(_atd_print_extras(a, data, api_user=api_user))
