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
    return jsonify(_atd_json(a, detail=True))


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
@api_auth_required("atd.submit")
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
@api_auth_required("atd.cancel")
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
