"""Attachment endpoints for React master-data screens.

Flask exposes upload / list / download / delete on vehicle and driver
detail/form pages; React inherits the same behaviour.

Every rule about what may be uploaded lives in AttachmentService, driven
by System Parameters (ATTACHMENT_ALLOWED_EXTENSIONS,
ATTACHMENT_MAX_SIZE_MB). None of it is restated here. Re-checking an
extension in this layer would create a second definition of "an
acceptable file" that drifts from the one the Jinja upload enforces the
moment an admin changes a setting.

For the same reason `/attachments/rules` exists: the frontend needs to
tell the user what is allowed BEFORE they pick a file, and the only
honest source for that is the server. A hardcoded list in React would
accept a file the server then rejects, which reads as a broken upload.
"""
from io import BytesIO

from flask import jsonify, request, send_file

from app.modules.api.auth import api_auth_required
from app.modules.api.routes import bp


def _bad(message, code=400, kind="validation"):
    return jsonify({"error": kind, "message": message}), code


def _not_found(kind="Record"):
    return jsonify({"error": "not_found",
                    "message": f"{kind} not found or not visible to "
                               "this account."}), 404


def _visible_driver(driver_id, api_user):
    from app.modules.master_data.driver.service import DriverService
    return DriverService().get_visible(driver_id, api_user)


def _visible_tire(tire_id, api_user):
    from app.modules.master_data.tire.service import TireService
    return TireService().get_visible(tire_id, api_user)


def _visible_battery(battery_id, api_user):
    from app.modules.master_data.battery.service import BatteryService
    return BatteryService().get_visible(battery_id, api_user)


def _visible_maintenance_order(order_id, api_user):
    """Reuses list_filtered's org-scope rule rather than a second,
    potentially drifting definition of who may see which order. Less
    efficient than a targeted query, but this endpoint is checked once
    per attachment request, not once per row of a list."""
    from app.modules.transactions.maintenance_order.models import (
        MaintenanceOrder)
    from app.modules.transactions.maintenance_order.service import (
        MaintenanceOrderService)
    order = MaintenanceOrder.query.filter_by(id=order_id).first()
    if order is None:
        return None
    visible_ids = {o.id for o in MaintenanceOrderService().list_filtered(
        user=api_user, page=1, per_page=100000)[0]}
    return order if order.id in visible_ids else None


def _visible_trip_ticket(trip_id, api_user):
    """Reuses list_filtered's org-scope rule, same as the other
    transaction helpers here."""
    from app.modules.transactions.trip_ticket.models import TripTicket
    from app.modules.transactions.trip_ticket.service import TripTicketService
    trip = TripTicket.query.filter_by(id=trip_id).first()
    if trip is None:
        return None
    visible_ids = {t.id for t in TripTicketService().list_filtered(
        user=api_user, page=1, per_page=100000)[0]}
    return trip if trip.id in visible_ids else None


def _visible_purchase_request(pr_id, api_user):
    from app.modules.transactions.purchase_request.models import (
        PurchaseRequest)
    from app.modules.transactions.purchase_request.service import (
        PurchaseRequestService)
    pr = PurchaseRequest.query.filter_by(id=pr_id).first()
    if pr is None:
        return None
    visible_ids = {p.id for p in PurchaseRequestService().list_filtered(
        user=api_user, page=1, per_page=100000)[0]}
    return pr if pr.id in visible_ids else None


def _parent_visible(reference_table, reference_id, api_user):
    """Visibility of the parent record an attachment hangs off."""
    if reference_table == "vehicles":
        return _visible_vehicle(reference_id, api_user) is not None
    if reference_table == "drivers":
        return _visible_driver(reference_id, api_user) is not None
    if reference_table == "tires":
        return _visible_tire(reference_id, api_user) is not None
    if reference_table == "batteries":
        return _visible_battery(reference_id, api_user) is not None
    if reference_table == "maintenance_orders":
        return _visible_maintenance_order(reference_id, api_user) is not None
    if reference_table == "purchase_requests":
        return _visible_purchase_request(reference_id, api_user) is not None
    if reference_table == "trip_tickets":
        return _visible_trip_ticket(reference_id, api_user) is not None
    return False


def _can(api_user, code: str) -> bool:
    check = getattr(api_user, "has_permission", None)
    if callable(check):
        return bool(check(code))
    # Some User shapes expose permissions as a set/list of codes.
    perms = getattr(api_user, "permissions", None) or []
    if isinstance(perms, (set, list, tuple)):
        return code in perms
    return False



def _attachment_json(a):
    return {
        "id": a.id,
        "original_filename": a.original_filename,
        "filename": a.filename,
        "file_size": a.file_size,
        "mime_type": a.mime_type,
        "uploaded_by": a.uploaded_by,
        "uploaded_at": a.created_at.isoformat() if a.created_at else None,
        "document_type": getattr(a, "document_type", None),
    }


def _visible_vehicle(vehicle_id, api_user):
    from app.modules.master_data.vehicle.service import VehicleService
    return VehicleService().get_visible(vehicle_id, api_user)


@bp.route("/attachments/rules", methods=["GET"])
@api_auth_required()
def attachment_rules(api_user):
    """What the server will accept, so the client can say so up front."""
    from app.core.attachments.attachment_service import AttachmentService
    svc = AttachmentService()
    return jsonify({
        "allowed_extensions": sorted(
            e.strip().lstrip(".").lower() for e in svc._allowed if e.strip()),
        "max_size_mb": round(svc._max_bytes / (1024 * 1024), 2),
        "max_size_bytes": svc._max_bytes,
        # From Lookup Maintenance (ATTACHMENT_DOC_TYPE), so an admin can
        # add a type without a frontend deploy. A list hardcoded in
        # React would go stale the moment they did, and the server would
        # then reject a type its own UI offered.
        "document_types": [
            {"code": row.code, "label": getattr(row, "label", None) or row.code}
            for row in svc.document_types()
        ],
    })


@bp.route("/vehicles/<int:vehicle_id>/attachments", methods=["GET"])
@api_auth_required("vehicle.view")
def vehicle_attachments(api_user, vehicle_id):
    if _visible_vehicle(vehicle_id, api_user) is None:
        # Same answer as the detail endpoint. A file list for an
        # invisible vehicle would leak both that it exists and what
        # documents it holds.
        return _not_found()
    from app.core.attachments.attachment_service import AttachmentService
    rows = AttachmentService().list_for("vehicles", vehicle_id)
    return jsonify({"items": [_attachment_json(a) for a in rows]})


@bp.route("/vehicles/<int:vehicle_id>/attachments", methods=["POST"])
@api_auth_required("vehicle.update")
def upload_vehicle_attachment(api_user, vehicle_id):
    """Attach a document. Requires vehicle.update, not vehicle.view --
    being able to read a vehicle does not imply the right to add
    documents to its record."""
    if _visible_vehicle(vehicle_id, api_user) is None:
        return _not_found()

    file = request.files.get("file")
    if file is None or not file.filename:
        return _bad("No file was uploaded.", kind="bad_request")

    from app.core.attachments.attachment_service import (AttachmentError,
                                                         AttachmentService)
    try:
        attachment = AttachmentService().upload(
            file, "vehicles", vehicle_id, user=api_user,
            document_type=(request.form.get("document_type") or None))
    except AttachmentError as exc:
        # The service owns the wording; this layer only chooses the
        # status code.
        return _bad(str(exc))
    return jsonify(_attachment_json(attachment)), 201


def _attachment_for(attachment_id, api_user):
    """Load an attachment, enforcing the parent record's visibility.

    Checked through the parent rather than the attachment row, because
    the attachment carries no scope of its own -- its confidentiality is
    entirely the parent record's (vehicle or driver).
    """
    from app.extensions import db
    from app.core.models.attachment import Attachment

    att = db.session.get(Attachment, attachment_id)
    if att is None or not att.is_active:
        return None
    if not _parent_visible(att.reference_table, att.reference_id, api_user):
        return None
    return att


@bp.route("/attachments/<int:attachment_id>/download", methods=["GET"])
@api_auth_required()
def download_attachment(api_user, attachment_id):
    """Download any attachment the caller can see via its parent.

    Permission is the parent's view right (vehicle.view or driver.view),
    enforced inside _attachment_for via visibility rather than a single
    hardcoded code -- a driver-only account must still open photographs.
    """
    att = _attachment_for(attachment_id, api_user)
    if att is None:
        return jsonify({"error": "not_found",
                        "message": "Attachment not found."}), 404
    from app.core.attachments.attachment_service import AttachmentService
    # Bytes come from the service, never from att.file_data directly.
    # That is THE storage boundary: production storage is not yet chosen,
    # and every direct column read is another place that would need
    # editing to move attachments to object storage. An existing test
    # enforces this, and it caught me reading the column here.
    data = AttachmentService().get_bytes(att) or b""
    return send_file(
        BytesIO(data),
        as_attachment=True,
        # The stored name is uniquified to avoid collisions; the user
        # gets back the name they uploaded.
        download_name=att.original_filename or att.filename,
        mimetype=att.mime_type or "application/octet-stream")


@bp.route("/attachments/<int:attachment_id>", methods=["DELETE"])
@api_auth_required()
def delete_attachment(api_user, attachment_id):
    """Soft-delete. Parent update permission required."""
    att = _attachment_for(attachment_id, api_user)
    if att is None:
        return jsonify({"error": "not_found",
                        "message": "Attachment not found."}), 404
    # maintenance_orders and purchase_requests added here for the same
    # reason tires/batteries use .create instead of .update: gate on
    # whichever permission this module actually has for "may modify
    # this record's supporting documents." Missing either of these two
    # entries would make `need` resolve to None for that table, and
    # the check below SKIPS permission enforcement entirely when `need`
    # is falsy -- confirmed by a failing test before this fix existed:
    # a mere viewer could delete an attachment on an order or request
    # they could not otherwise modify at all.
    need = {"vehicles": "vehicle.update", "drivers": "driver.update",
            "tires": "tire.create", "batteries": "battery.create",
            "maintenance_orders": "maintenanceorder.update",
            "purchase_requests": "purchaserequest.create",
            "trip_tickets": "tripticket.update"}.get(
        att.reference_table)
    if need and not _can(api_user, need):
        return jsonify({"error": "forbidden",
                        "message": "You do not have permission to remove "
                                   "this attachment."}), 403
    from app.core.attachments.attachment_service import AttachmentService
    # Soft delete, as the service does: the row survives so the audit
    # trail still has something to point at.
    AttachmentService().delete(attachment_id, user=api_user)
    return jsonify({"ok": True})


@bp.route("/drivers/<int:driver_id>/attachments", methods=["GET"])
@api_auth_required("driver.view")
def driver_attachments(api_user, driver_id):
    if _visible_driver(driver_id, api_user) is None:
        return _not_found("Driver")
    from app.core.attachments.attachment_service import AttachmentService
    try:
        rows = AttachmentService().list_for("drivers", driver_id)
    except Exception:
        # Missing table / migration should not blank the whole driver page.
        rows = []
    return jsonify({"items": [_attachment_json(a) for a in rows]})


@bp.route("/drivers/<int:driver_id>/attachments", methods=["POST"])
@api_auth_required("driver.update")
def upload_driver_attachment(api_user, driver_id):
    """Attach a document to a driver. Requires driver.update."""
    if _visible_driver(driver_id, api_user) is None:
        return _not_found("Driver")

    file = request.files.get("file")
    if file is None or not file.filename:
        return _bad("No file was uploaded.", kind="bad_request")

    from app.core.attachments.attachment_service import (AttachmentError,
                                                         AttachmentService)
    try:
        attachment = AttachmentService().upload(
            file, "drivers", driver_id, user=api_user,
            document_type=(request.form.get("document_type") or None))
    except AttachmentError as exc:
        return _bad(str(exc))
    return jsonify(_attachment_json(attachment)), 201




@bp.route("/tires/<int:tire_id>/attachments", methods=["GET"])
@api_auth_required("tire.view")
def tire_attachments(api_user, tire_id):
    if _visible_tire(tire_id, api_user) is None:
        return _not_found("Tire")
    from app.core.attachments.attachment_service import AttachmentService
    rows = AttachmentService().list_for("tires", tire_id)
    return jsonify({"items": [_attachment_json(a) for a in rows]})


@bp.route("/tires/<int:tire_id>/attachments", methods=["POST"])
@api_auth_required("tire.create")
def upload_tire_attachment(api_user, tire_id):
    if _visible_tire(tire_id, api_user) is None:
        return _not_found("Tire")
    file = request.files.get("file")
    if file is None or not file.filename:
        return _bad("No file was uploaded.", kind="bad_request")
    from app.core.attachments.attachment_service import (AttachmentError,
                                                         AttachmentService)
    try:
        attachment = AttachmentService().upload(
            file, "tires", tire_id, user=api_user,
            document_type=(request.form.get("document_type") or None))
    except AttachmentError as exc:
        return _bad(str(exc))
    return jsonify(_attachment_json(attachment)), 201



@bp.route("/batteries/<int:battery_id>/attachments", methods=["GET"])
@api_auth_required("battery.view")
def battery_attachments(api_user, battery_id):
    if _visible_battery(battery_id, api_user) is None:
        return _not_found("Battery")
    from app.core.attachments.attachment_service import AttachmentService
    rows = AttachmentService().list_for("batteries", battery_id)
    return jsonify({"items": [_attachment_json(a) for a in rows]})


@bp.route("/batteries/<int:battery_id>/attachments", methods=["POST"])
@api_auth_required("battery.create")
def upload_battery_attachment(api_user, battery_id):
    if _visible_battery(battery_id, api_user) is None:
        return _not_found("Battery")
    file = request.files.get("file")
    if file is None or not file.filename:
        return _bad("No file was uploaded.", kind="bad_request")
    from app.core.attachments.attachment_service import (AttachmentError,
                                                         AttachmentService)
    try:
        attachment = AttachmentService().upload(
            file, "batteries", battery_id, user=api_user,
            document_type=(request.form.get("document_type") or None))
    except AttachmentError as exc:
        return _bad(str(exc))
    return jsonify(_attachment_json(attachment)), 201


@bp.route("/maintenance-orders/<int:order_id>/attachments", methods=["GET"])
@api_auth_required("maintenanceorder.view")
def maintenance_order_attachments(api_user, order_id):
    if _visible_maintenance_order(order_id, api_user) is None:
        return _not_found("Maintenance Order")
    from app.core.attachments.attachment_service import AttachmentService
    rows = AttachmentService().list_for("maintenance_orders", order_id)
    return jsonify({"items": [_attachment_json(a) for a in rows]})


@bp.route("/maintenance-orders/<int:order_id>/attachments", methods=["POST"])
@api_auth_required("maintenanceorder.update")
def upload_maintenance_order_attachment(api_user, order_id):
    """Requires maintenanceorder.update, not .view -- being able to
    read an order does not imply the right to attach documents to its
    record, matching the same reasoning already recorded on vehicles'
    upload endpoint."""
    if _visible_maintenance_order(order_id, api_user) is None:
        return _not_found("Maintenance Order")

    file = request.files.get("file")
    if file is None or not file.filename:
        return _bad("No file was uploaded.", kind="bad_request")

    from app.core.attachments.attachment_service import (AttachmentError,
                                                         AttachmentService)
    try:
        attachment = AttachmentService().upload(
            file, "maintenance_orders", order_id, user=api_user,
            document_type=(request.form.get("document_type") or None))
    except AttachmentError as exc:
        return _bad(str(exc))
    return jsonify(_attachment_json(attachment)), 201


@bp.route("/purchase-requests/<int:pr_id>/attachments", methods=["GET"])
@api_auth_required("purchaserequest.view")
def purchase_request_attachments(api_user, pr_id):
    if _visible_purchase_request(pr_id, api_user) is None:
        return _not_found("Purchase Request")
    from app.core.attachments.attachment_service import AttachmentService
    rows = AttachmentService().list_for("purchase_requests", pr_id)
    return jsonify({"items": [_attachment_json(a) for a in rows]})


@bp.route("/purchase-requests/<int:pr_id>/attachments", methods=["POST"])
@api_auth_required("purchaserequest.create")
def upload_purchase_request_attachment(api_user, pr_id):
    """Requires purchaserequest.create, not .view. There is no
    purchaserequest.update permission for this action to hang off of
    the way maintenanceorder.update covers MO -- create is the closest
    real permission this module has for "may modify this record's
    supporting documents," matching how the create-time line-editing
    guards on this same model are gated."""
    if _visible_purchase_request(pr_id, api_user) is None:
        return _not_found("Purchase Request")

    file = request.files.get("file")
    if file is None or not file.filename:
        return _bad("No file was uploaded.", kind="bad_request")

    from app.core.attachments.attachment_service import (AttachmentError,
                                                         AttachmentService)
    try:
        attachment = AttachmentService().upload(
            file, "purchase_requests", pr_id, user=api_user,
            document_type=(request.form.get("document_type") or None))
    except AttachmentError as exc:
        return _bad(str(exc))
    return jsonify(_attachment_json(attachment)), 201


@bp.route("/trip-tickets/<int:trip_id>/attachments", methods=["GET"])
@api_auth_required("tripticket.view")
def trip_ticket_attachments(api_user, trip_id):
    if _visible_trip_ticket(trip_id, api_user) is None:
        return _not_found("Trip Ticket")
    from app.core.attachments.attachment_service import AttachmentService
    rows = AttachmentService().list_for("trip_tickets", trip_id)
    return jsonify({"items": [_attachment_json(a) for a in rows]})


@bp.route("/trip-tickets/<int:trip_id>/attachments", methods=["POST"])
@api_auth_required("tripticket.update")
def upload_trip_ticket_attachment(api_user, trip_id):
    if _visible_trip_ticket(trip_id, api_user) is None:
        return _not_found("Trip Ticket")

    file = request.files.get("file")
    if file is None or not file.filename:
        return _bad("No file was uploaded.", kind="bad_request")

    from app.core.attachments.attachment_service import (AttachmentError,
                                                         AttachmentService)
    try:
        attachment = AttachmentService().upload(
            file, "trip_tickets", trip_id, user=api_user,
            document_type=(request.form.get("document_type") or None))
    except AttachmentError as exc:
        return _bad(str(exc))
    return jsonify(_attachment_json(attachment)), 201
