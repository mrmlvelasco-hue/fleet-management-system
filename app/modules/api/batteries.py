"""Battery master endpoints for the React Master Data screens.

Mirrors tires.py: list/create/update/deactivate/summary/export.
Jinja has list + create + deactivate only; form template + service.update
support edit — React exposes both. Edit gated on battery.create (no
battery.update in seed).
"""
from datetime import date
from io import BytesIO

from flask import jsonify, request, send_file

from app.modules.api.auth import api_auth_required
from app.modules.api.coercion import Coercer, FieldValueError
from app.modules.api.routes import bp

_BATTERY_STATUSES = ("IN_STOCK", "MOUNTED", "DISPOSED")

_WRITABLE = [
    "serial_number", "brand", "capacity_ah", "voltage",
    "purchase_date", "purchase_cost", "vendor_id", "branch_id",
    "company_id", "status", "vehicle_id",
]

_COERCER = Coercer(
    ints={"vendor_id": "Vendor", "branch_id": "Branch",
          "company_id": "Company", "vehicle_id": "Vehicle",
          "capacity_ah": "Capacity (Ah)", "voltage": "Voltage"},
    dates={"purchase_date": "Purchase Date"},
    decimals={"purchase_cost": "Purchase Cost"},
)


def _bad(message, code=400):
    return jsonify({"error": "bad_request", "message": message}), code


def _effective_company(b):
    """Resolved Company + Region/Area-District -- {} when unresolved."""
    from app.core.company_resolution import effective_company_id
    from app.extensions import db
    from app.modules.master_data.org.models import OrgCompany
    co_id = effective_company_id(b)
    co = db.session.get(OrgCompany, co_id) if co_id else None
    if co is None:
        return {}
    return {"id": co.id, "short_name": co.short_name,
            "region": co.region.name if co.region else None,
            "area_district": co.area_district.name
                if co.area_district else None}


def _serialise(b):
    co = _effective_company(b)
    return {
        "id": b.id,
        "serial_number": b.serial_number,
        "brand": b.brand,
        "capacity_ah": b.capacity_ah,
        "voltage": b.voltage,
        "status": b.status,
        "is_active": bool(b.is_active),
        "branch": b.branch.name if b.branch else None,
        "branch_id": b.branch_id,
        "company_id": b.company_id,
        "company": co.get("short_name"),
        "region": co.get("region"),
        "area_district": co.get("area_district"),
        "vendor": b.vendor.name if b.vendor else None,
        "vendor_id": b.vendor_id,
        "purchase_date": b.purchase_date.isoformat() if b.purchase_date else None,
        "purchase_cost": float(b.purchase_cost) if b.purchase_cost is not None else None,
        "vehicle_id": b.vehicle_id,
        "vehicle_plate": (
            (b.vehicle.plate_number or b.vehicle.conduction_number)
            if b.vehicle else None),
    }


def _common_filters():
    """Shared query-string parsing for list/summary, independent of
    pagination. Returns (kwargs, error)."""
    status = (request.args.get("status") or "").strip().upper()
    q = (request.args.get("q") or "").strip()
    try:
        branch_id = request.args.get("branch_id")
        branch_id = int(branch_id) if branch_id else None
        company_id = request.args.get("company_id")
        company_id = int(company_id) if company_id else None
    except (TypeError, ValueError):
        return None, _bad("branch_id and company_id must be integers.")

    if status and status not in _BATTERY_STATUSES:
        return None, _bad(f"Unknown status '{status}'.")

    return dict(status=status or None, search=q or None,
               branch_id=branch_id, company_id=company_id), None


def _filtered(api_user):
    """Kept for batteries_export(), which needs every matching row
    rather than one page -- out of scope for this fix (see the
    2026-11-08 performance audit, section 2.2)."""
    from app.modules.master_data.battery.service import BatteryService

    filters, error = _common_filters()
    if error is not None:
        return None, error

    rows = BatteryService().list(
        include_inactive=True, status=filters["status"],
        user=api_user, branch_id=filters["branch_id"],
        company_id=filters["company_id"])

    if filters["search"]:
        q = filters["search"].lower()
        rows = [b for b in rows if q in " ".join(filter(None, [
            b.serial_number, b.brand,
            str(b.capacity_ah) if b.capacity_ah is not None else None,
            str(b.voltage) if b.voltage is not None else None,
            b.branch.name if b.branch else None,
        ])).lower()]
    return rows, None


@bp.route("/batteries", methods=["GET"])
@api_auth_required("battery.view")
def list_batteries(api_user):
    from app.modules.master_data.battery.service import BatteryService

    filters, error = _common_filters()
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

    rows, total = BatteryService().list_page(
        include_inactive=True, user=api_user, page=page,
        page_size=page_size, **filters)
    return jsonify({
        "items": [_serialise(b) for b in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, (total + page_size - 1) // page_size),
    })


@bp.route("/batteries/summary", methods=["GET"])
@api_auth_required("battery.view")
def batteries_summary(api_user):
    from app.modules.master_data.battery.service import BatteryService

    filters, error = _common_filters()
    if error is not None:
        return error
    return jsonify(BatteryService().summary(
        include_inactive=True, user=api_user, **filters))


@bp.route("/batteries/export.xlsx", methods=["GET"])
@api_auth_required("battery.view")
def batteries_export(api_user):
    rows, error = _filtered(api_user)
    if error is not None:
        return error
    try:
        from openpyxl import Workbook
    except ImportError:
        return _bad("Excel export is not available on this server.", 501)

    wb = Workbook()
    ws = wb.active
    ws.title = "Batteries"
    ws.append(["Serial", "Brand", "Capacity (Ah)", "Voltage", "Company",
               "Region", "Area / District", "Status", "Active",
               "Purchase Date", "Purchase Cost"])
    for b in rows:
        co = _effective_company(b)
        ws.append([
            b.serial_number, b.brand, b.capacity_ah, b.voltage,
            co.get("short_name"), co.get("region"), co.get("area_district"),
            b.status,
            "Yes" if b.is_active else "No",
            b.purchase_date.isoformat() if b.purchase_date else None,
            float(b.purchase_cost) if b.purchase_cost is not None else None,
        ])
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(
        buf, as_attachment=True,
        download_name=f"batteries_{date.today().isoformat()}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument"
                 ".spreadsheetml.sheet")


@bp.route("/batteries/<int:battery_id>", methods=["GET"])
@api_auth_required("battery.view")
def battery_detail(api_user, battery_id):
    from app.modules.master_data.battery.service import BatteryService
    b = BatteryService().get_visible(battery_id, api_user)
    if b is None:
        return jsonify({"error": "not_found",
                        "message": "Battery not found or not visible to "
                                   "this account."}), 404
    return jsonify(_serialise(b))


def _validation_response(exc):
    field = "_"
    if isinstance(exc, FieldValueError):
        field = exc.field
    text = str(exc).lower()
    if "serial" in text:
        field = "serial_number"
    return jsonify({"error": "validation", "message": str(exc),
                    "fields": {field: str(exc)}}), 400


@bp.route("/batteries", methods=["POST"])
@api_auth_required("battery.create")
def create_battery(api_user):
    from app.modules.master_data.battery.service import (
        BatteryService, DuplicateSerialError)

    payload = request.get_json(silent=True) or {}
    try:
        fields = _COERCER.fields(payload, _WRITABLE)
        for req in ("serial_number", "brand"):
            if not fields.get(req):
                return jsonify({"error": "validation",
                                "message": f"{req} is required.",
                                "fields": {req: "Required."}}), 400
        from app.modules.api.coercion import org_anchor_error
        anchor_error = org_anchor_error(fields, "battery")
        if anchor_error:
            return anchor_error
        battery = BatteryService().create(
            serial_number=fields.pop("serial_number"),
            brand=fields.pop("brand"),
            **fields)
    except (DuplicateSerialError, FieldValueError, TypeError) as exc:
        return _validation_response(exc)
    return jsonify(_serialise(battery)), 201


@bp.route("/batteries/<int:battery_id>", methods=["PUT", "PATCH"])
@api_auth_required("battery.create")
def update_battery(api_user, battery_id):
    """Edit gated on battery.create — no battery.update in Jinja seed."""
    from app.modules.master_data.battery.service import (
        BatteryService, DuplicateSerialError)
    from app.modules.master_data.battery.models import Battery

    svc = BatteryService()
    if svc.get_visible(battery_id, api_user) is None:
        return jsonify({"error": "not_found",
                        "message": "Battery not found or not visible to "
                                   "this account."}), 404
    payload = request.get_json(silent=True) or {}
    try:
        fields = _COERCER.fields(payload, _WRITABLE)
        if "serial_number" in fields:
            other = Battery.query.filter_by(
                serial_number=fields["serial_number"]).first()
            if other and other.id != battery_id:
                raise DuplicateSerialError(
                    f"Battery serial number '{fields['serial_number']}' "
                    f"already exists.")
        battery = svc.update(battery_id, **fields)
    except (DuplicateSerialError, FieldValueError) as exc:
        return _validation_response(exc)
    return jsonify(_serialise(battery))


@bp.route("/batteries/<int:battery_id>/deactivate", methods=["POST"])
@api_auth_required("battery.delete")
def deactivate_battery(api_user, battery_id):
    from app.modules.master_data.battery.service import BatteryService
    svc = BatteryService()
    if svc.get_visible(battery_id, api_user) is None:
        return jsonify({"error": "not_found",
                        "message": "Battery not found or not visible to "
                                   "this account."}), 404
    svc.deactivate(battery_id)
    return jsonify({"ok": True})
