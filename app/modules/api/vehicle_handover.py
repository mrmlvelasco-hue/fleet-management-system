"""Vehicle Handover Checklist API.

Standalone from /api/v1/checklists (app/modules/api/checklists.py) --
see app/modules/transactions/vehicle_handover/models.py for why. This
file must not import anything from that sibling module or from
app.modules.transactions.vehicle_checklist.

Permission codes follow the project's five registered actions only
(view, create, update, delete, print) -- no invented codes.
"""
from flask import jsonify, request

from app.core.security.registry import registry
from app.modules.api.auth import api_auth_required
from app.modules.api.routes import bp
from app.modules.transactions.vehicle_handover.models import (
    VehicleHandoverChecklist)
from app.modules.transactions.vehicle_handover.service import (
    VehicleHandoverService)

for _code, _desc in [
    ("vehiclehandover.view", "View Vehicle Handover Checklists"),
    ("vehiclehandover.create", "Create Vehicle Handover Checklists"),
    ("vehiclehandover.update", "Update Vehicle Handover Checklists "
                               "(issue, return, mark items)"),
    ("vehiclehandover.delete", "Delete Vehicle Handover Checklists"),
    ("vehiclehandover.print", "Print Vehicle Handover Checklists"),
]:
    _m, _a = _code.split(".")
    registry.register(_code, _m, _a, _desc)


def _not_found(label="Vehicle Handover Checklist"):
    return jsonify({"error": "not_found",
                    "message": f"No {label.lower()} found."}), 404


def _bad(message, kind="bad_request"):
    return jsonify({"error": kind, "message": message}), 400


def _row(doc: VehicleHandoverChecklist) -> dict:
    return {
        "id": doc.id,
        "document_number": doc.document_number,
        "vehicle_id": doc.vehicle_id,
        "plate_number": doc.vehicle.plate_number if doc.vehicle else None,
        "vehicle_label": (f"{doc.vehicle.brand} {doc.vehicle.model}"
                         if doc.vehicle else None),
        "assignee_name": doc.assignee_name,
        "status": doc.status,
        "date_issued": doc.date_issued.isoformat() if doc.date_issued else None,
        "date_returned": doc.date_returned.isoformat() if doc.date_returned else None,
        "is_active": doc.is_active,
    }


@bp.route("/vehicle-handovers", methods=["GET"])
@api_auth_required("vehiclehandover.view")
def list_handovers(api_user):
    rows = VehicleHandoverService().list_visible(api_user)
    return jsonify({"items": [_row(d) for d in rows], "total": len(rows)})


@bp.route("/vehicle-handovers", methods=["POST"])
@api_auth_required("vehiclehandover.create")
def create_handover(api_user):
    p = request.get_json(silent=True) or {}
    vehicle_id = p.get("vehicle_id")
    if not vehicle_id:
        return _bad("vehicle_id is required.")
    try:
        doc = VehicleHandoverService().create(
            vehicle_id=vehicle_id,
            assignee_driver_id=p.get("assignee_driver_id"),
            actor=api_user)
    except ValueError as exc:
        return _bad(str(exc))
    return jsonify(_row(doc)), 201


@bp.route("/vehicle-handovers/<int:hid>", methods=["GET"])
@api_auth_required("vehiclehandover.view")
def get_handover(api_user, hid):
    doc = VehicleHandoverService().get_visible(hid, api_user)
    if doc is None:
        return _not_found()
    return jsonify(VehicleHandoverService().to_report(hid) | _row(doc))


@bp.route("/vehicle-handovers/<int:hid>/report", methods=["GET"])
@api_auth_required("vehiclehandover.print")
def get_handover_report(api_user, hid):
    """The exact payload shape react-v191's VehicleChecklistReport
    expects -- see VehicleHandoverService.to_report()."""
    doc = VehicleHandoverService().get_visible(hid, api_user)
    if doc is None:
        return _not_found()
    return jsonify(VehicleHandoverService().to_report(hid))


@bp.route("/vehicle-handovers/<int:hid>/issue", methods=["POST"])
@api_auth_required("vehiclehandover.update")
def issue_handover(api_user, hid):
    if VehicleHandoverService().get_visible(hid, api_user) is None:
        return _not_found()
    p = request.get_json(silent=True) or {}
    try:
        doc = VehicleHandoverService().issue(
            hid, odometer=p.get("odometer"), fuel_level=p.get("fuel_level"),
            issued_by_name=p.get("issued_by_name"),
            received_by_name=p.get("received_by_name"), actor=api_user)
    except ValueError as exc:
        return _bad(str(exc))
    return jsonify(_row(doc))


@bp.route("/vehicle-handovers/<int:hid>/return", methods=["POST"])
@api_auth_required("vehiclehandover.update")
def return_handover(api_user, hid):
    if VehicleHandoverService().get_visible(hid, api_user) is None:
        return _not_found()
    p = request.get_json(silent=True) or {}
    try:
        doc = VehicleHandoverService().return_unit(
            hid, odometer=p.get("odometer"),
            returned_by_name=p.get("returned_by_name"),
            received_by_name=p.get("received_by_name"), actor=api_user)
    except ValueError as exc:
        return _bad(str(exc))
    return jsonify(_row(doc))


@bp.route("/vehicle-handovers/<int:hid>/items/<int:item_id>",
         methods=["PATCH"])
@api_auth_required("vehiclehandover.update")
def mark_handover_item(api_user, hid, item_id):
    if VehicleHandoverService().get_visible(hid, api_user) is None:
        return _not_found()
    p = request.get_json(silent=True) or {}
    phase = p.get("phase")
    try:
        item = VehicleHandoverService().mark_item(
            item_id, phase=phase, mark=p.get("mark"), qty=p.get("qty"),
            actor=api_user)
    except ValueError as exc:
        return _bad(str(exc))
    return jsonify({
        "id": item.id, "item_name": item.item_name,
        "issuance_mark": item.issuance_mark, "issuance_qty": item.issuance_qty,
        "return_mark": item.return_mark, "return_qty": item.return_qty,
    })


@bp.route("/vehicle-handovers/<int:hid>/damages", methods=["POST"])
@api_auth_required("vehiclehandover.update")
def add_handover_damage(api_user, hid):
    if VehicleHandoverService().get_visible(hid, api_user) is None:
        return _not_found()
    p = request.get_json(silent=True) or {}
    try:
        mark = VehicleHandoverService().add_damage(
            hid, kind=p.get("kind"), x=p.get("x"), y=p.get("y"),
            note=p.get("note"), actor=api_user)
    except (ValueError, TypeError) as exc:
        return _bad(str(exc))
    return jsonify({"id": mark.id, "kind": mark.kind, "x": mark.x,
                    "y": mark.y, "note": mark.note}), 201


@bp.route("/vehicle-handovers/<int:hid>/damages/<int:mark_id>",
         methods=["DELETE"])
@api_auth_required("vehiclehandover.update")
def remove_handover_damage(api_user, hid, mark_id):
    if VehicleHandoverService().get_visible(hid, api_user) is None:
        return _not_found()
    VehicleHandoverService().remove_damage(mark_id, actor=api_user)
    return "", 204
