"""Tire master endpoints for the React Master Data screens.

Built from tire_list.html / tire_form.html and TireService. Flask Jinja
currently has list + create + deactivate only; the form template and
service already support edit (item, update()). React inherits list/create
/deactivate and adds edit so the form template is not dead code.

Envelope matches vehicles/drivers so the same list scaffold applies.
"""
from datetime import date
from io import BytesIO

from flask import jsonify, request, send_file

from app.modules.api.auth import api_auth_required
from app.modules.api.coercion import Coercer, FieldValueError
from app.modules.api.routes import bp

_TIRE_STATUSES = ("IN_STOCK", "MOUNTED", "RETREADED", "DISPOSED")

_WRITABLE = [
    "serial_number", "brand", "size", "tire_type",
    "purchase_date", "purchase_cost", "tread_depth_initial",
    "vendor_id", "branch_id", "status",
    "vehicle_id", "wheel_position",
]

_COERCER = Coercer(
    ints={"vendor_id": "Vendor", "branch_id": "Branch",
          "vehicle_id": "Vehicle"},
    dates={"purchase_date": "Purchase Date"},
    decimals={"purchase_cost": "Purchase Cost",
              "tread_depth_initial": "Tread Depth (Initial)"},
)


def _bad(message, code=400):
    return jsonify({"error": "bad_request", "message": message}), code


def _serialise(t):
    return {
        "id": t.id,
        "serial_number": t.serial_number,
        "brand": t.brand,
        "size": t.size,
        "tire_type": t.tire_type,
        "status": t.status,
        "is_active": bool(t.is_active),
        "branch": t.branch.name if t.branch else None,
        "branch_id": t.branch_id,
        "vendor": t.vendor.name if t.vendor else None,
        "vendor_id": t.vendor_id,
        "purchase_date": t.purchase_date.isoformat() if t.purchase_date else None,
        "purchase_cost": float(t.purchase_cost) if t.purchase_cost is not None else None,
        "tread_depth_initial": float(t.tread_depth_initial)
            if t.tread_depth_initial is not None else None,
        "vehicle_id": t.vehicle_id,
        "wheel_position": t.wheel_position,
        "vehicle_plate": (
            (t.vehicle.plate_number or t.vehicle.conduction_number)
            if t.vehicle else None),
    }


def _filtered(api_user):
    from app.modules.master_data.tire.service import TireService

    status = (request.args.get("status") or "").strip().upper()
    tire_type = (request.args.get("tire_type") or "").strip().upper()
    q = (request.args.get("q") or "").strip().lower()
    try:
        branch_id = request.args.get("branch_id")
        branch_id = int(branch_id) if branch_id else None
    except (TypeError, ValueError):
        return None, _bad("branch_id must be an integer.")

    if status and status not in _TIRE_STATUSES:
        return None, _bad(f"Unknown status '{status}'.")

    rows = TireService().list(
        include_inactive=True, status=status or None,
        user=api_user, branch_id=branch_id)

    if tire_type:
        rows = [t for t in rows if (t.tire_type or "").upper() == tire_type]
    if q:
        rows = [t for t in rows if q in " ".join(filter(None, [
            t.serial_number, t.brand, t.size, t.tire_type,
            t.branch.name if t.branch else None,
        ])).lower()]
    return rows, None


@bp.route("/tires", methods=["GET"])
@api_auth_required("tire.view")
def list_tires(api_user):
    rows, error = _filtered(api_user)
    if error is not None:
        return error
    try:
        page = int(request.args.get("page", 1))
        page_size = int(request.args.get("page_size", 20))
    except (TypeError, ValueError):
        return _bad("page and page_size must be integers.")
    if page < 1 or page_size < 1:
        return _bad("page and page_size must be 1 or greater.")
    page_size = min(page_size, 200)
    total = len(rows)
    start = (page - 1) * page_size
    window = rows[start:start + page_size]
    return jsonify({
        "items": [_serialise(t) for t in window],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, (total + page_size - 1) // page_size),
    })


@bp.route("/tires/summary", methods=["GET"])
@api_auth_required("tire.view")
def tires_summary(api_user):
    rows, error = _filtered(api_user)
    if error is not None:
        return error
    return jsonify({
        "total": len(rows),
        "in_stock": sum(1 for t in rows if t.status == "IN_STOCK"),
        "mounted": sum(1 for t in rows if t.status == "MOUNTED"),
        "retreaded": sum(1 for t in rows if t.status == "RETREADED"),
        "disposed": sum(1 for t in rows if t.status == "DISPOSED"),
        "active": sum(1 for t in rows if t.is_active),
    })


@bp.route("/tires/export.xlsx", methods=["GET"])
@api_auth_required("tire.view")
def tires_export(api_user):
    rows, error = _filtered(api_user)
    if error is not None:
        return error
    try:
        from openpyxl import Workbook
    except ImportError:
        return _bad("Excel export is not available on this server.", 501)

    wb = Workbook()
    ws = wb.active
    ws.title = "Tires"
    headers = ["Serial", "Brand", "Size", "Type", "Branch", "Status",
               "Active", "Purchase Date", "Purchase Cost", "Tread Depth"]
    ws.append(headers)
    for t in rows:
        ws.append([
            t.serial_number, t.brand, t.size, t.tire_type,
            t.branch.name if t.branch else None, t.status,
            "Yes" if t.is_active else "No",
            t.purchase_date.isoformat() if t.purchase_date else None,
            float(t.purchase_cost) if t.purchase_cost is not None else None,
            float(t.tread_depth_initial) if t.tread_depth_initial is not None else None,
        ])
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(
        buf, as_attachment=True,
        download_name=f"tires_{date.today().isoformat()}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument"
                 ".spreadsheetml.sheet")


@bp.route("/tires/<int:tire_id>", methods=["GET"])
@api_auth_required("tire.view")
def tire_detail(api_user, tire_id):
    from app.modules.master_data.tire.service import TireService
    t = TireService().get_visible(tire_id, api_user)
    if t is None:
        return jsonify({"error": "not_found",
                        "message": "Tire not found or not visible to "
                                   "this account."}), 404
    return jsonify(_serialise(t))


def _validation_response(exc):
    field = "_"
    if isinstance(exc, FieldValueError):
        field = exc.field
    text = str(exc).lower()
    if "serial" in text:
        field = "serial_number"
    return jsonify({"error": "validation", "message": str(exc),
                    "fields": {field: str(exc)}}), 400


@bp.route("/tires", methods=["POST"])
@api_auth_required("tire.create")
def create_tire(api_user):
    from app.modules.master_data.tire.service import (
        TireService, DuplicateSerialError)

    payload = request.get_json(silent=True) or {}
    try:
        fields = _COERCER.fields(payload, _WRITABLE)
        # Required by service signature
        for req in ("serial_number", "brand", "size", "tire_type"):
            if not fields.get(req):
                return jsonify({"error": "validation",
                                "message": f"{req} is required.",
                                "fields": {req: "Required."}}), 400
        tire = TireService().create(
            serial_number=fields.pop("serial_number"),
            brand=fields.pop("brand"),
            size=fields.pop("size"),
            tire_type=fields.pop("tire_type"),
            **fields)
    except (DuplicateSerialError, FieldValueError, TypeError) as exc:
        return _validation_response(exc)
    return jsonify(_serialise(tire)), 201


@bp.route("/tires/<int:tire_id>", methods=["PUT", "PATCH"])
@api_auth_required("tire.create")
def update_tire(api_user, tire_id):
    """Partial update. Jinja has no edit route yet; service.update exists.

    React uses this so the form template's edit shape is reachable.
    Jinja has no tire.update permission and no edit route. Edit is gated
    on tire.create (same people who can enrol a tire can correct it).
    When a dedicated tire.update code is seeded, switch this gate.
    """
    from app.modules.master_data.tire.service import (
        TireService, DuplicateSerialError)

    svc = TireService()
    if svc.get_visible(tire_id, api_user) is None:
        return jsonify({"error": "not_found",
                        "message": "Tire not found or not visible to "
                                   "this account."}), 404
    payload = request.get_json(silent=True) or {}
    try:
        fields = _COERCER.fields(payload, _WRITABLE)
        if "serial_number" in fields:
            other = __import__(
                "app.modules.master_data.tire.models", fromlist=["Tire"]
            ).Tire.query.filter_by(
                serial_number=fields["serial_number"]).first()
            if other and other.id != tire_id:
                raise DuplicateSerialError(
                    f"Tire serial number '{fields['serial_number']}' "
                    f"already exists.")
        tire = svc.update(tire_id, **fields)
    except (DuplicateSerialError, FieldValueError) as exc:
        return _validation_response(exc)
    return jsonify(_serialise(tire))


@bp.route("/tires/<int:tire_id>/deactivate", methods=["POST"])
@api_auth_required("tire.delete")
def deactivate_tire(api_user, tire_id):
    from app.modules.master_data.tire.service import TireService
    svc = TireService()
    if svc.get_visible(tire_id, api_user) is None:
        return jsonify({"error": "not_found",
                        "message": "Tire not found or not visible to "
                                   "this account."}), 404
    svc.deactivate(tire_id)
    return jsonify({"ok": True})
