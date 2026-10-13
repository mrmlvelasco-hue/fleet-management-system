"""The assignee-scoped namespace: /api/v1/my/*

Everything here answers "what is MINE", where mine means currently
assigned to the caller. Nothing here calls VehicleService.get_visible(),
which answers the org-scope question and is correct for the endpoints
that already use it.

WHY A SEPARATE NAMESPACE rather than narrowing /api/v1/vehicles/*:
those endpoints serve the web app, the telematics feed and external
integrations, all of which legitimately operate on org scope. Narrowing
them would break those callers. Adding a conditional branch inside them
would put two authorisation models in one function -- the thing that
later gets edited wrongly by someone who only read half of it. One scope
rule per endpoint, readable at a glance in review.

404 RATHER THAN 403 throughout. Inside /my/*, a vehicle you are not
assigned is not merely forbidden -- it is not part of your world. A 403
confirms the record exists, which turns sequential ids and plate numbers
into an enumeration oracle: a driver could map the whole fleet by
walking ids and reading the status codes. This deliberately differs from
the rest of the API, where 403 is right because the caller legitimately
knows the resource exists.

Permissions still apply on top. This namespace narrows WHICH RECORDS;
the permission codes decide WHICH ACTIONS; users.mobile_access decides
WHICH CHANNEL. Three independent questions, three independent
mechanisms, none of them doing another's job.
"""
from flask import jsonify, request

from app.extensions import db
from app.modules.api.auth import api_auth_required
from app.modules.api.routes import bp
from app.modules.master_data.vehicle.models import Vehicle
from app.modules.user_management.assignee_scope_service import (
    AssigneeScopeService)


def _not_found():
    """One refusal shape for every miss in this namespace.

    Identical for "does not exist", "exists but is not yours", and
    "was yours until last month". If those answered differently, the
    difference would be the leak.
    """
    return jsonify({"error": "not_found",
                    "message": "Vehicle not found."}), 404


def _assigned_vehicle(vehicle_id, api_user):
    """The vehicle, but only if the caller currently holds it.

    THE guard for this namespace. Deliberately one function rather than
    a check copied into each endpoint: a scope check written inline
    seven times is seven chances to omit the eighth, and the omission
    would be invisible -- the endpoint would work perfectly and return
    somebody else's data.
    """
    if not AssigneeScopeService().covers_vehicle(api_user, vehicle_id):
        return None
    return db.session.get(Vehicle, vehicle_id)


def _vehicle_label(vehicle_id):
    """Plate, or the conduction number when no plate exists yet.

    Philippine LTO: a new vehicle runs on its conduction number until
    the plate is released, and those are exactly the units most likely
    to be moved around on one-off authorities. Falling back is not a
    nicety -- without it a brand-new unit's ATD names nothing at all.

    Read directly rather than through the assignment guard: this labels
    a vehicle the caller has ALREADY been shown an authority for, and
    that authority may well cover a vehicle they are not assigned. A
    plate is not a secret; refusing to name it here would produce an
    authority document that identifies no vehicle.
    """
    if not vehicle_id:
        return None
    v = db.session.get(Vehicle, vehicle_id)
    if v is None:
        return None
    return v.plate_number or v.conduction_number


def _row(v, detail=False):
    data = {
        "id": v.id,
        "plate_number": v.plate_number,
        "conduction_number": v.conduction_number,
        "brand": v.brand,
        "model": v.model,
        "year": v.year,
        "current_odometer": v.current_odometer,
        "status": v.status,
    }
    if detail:
        data.update({
            "chassis_number": v.chassis_number,
            "engine_number": v.engine_number,
            "color": v.color,
            "fuel_type": v.fuel_type,
            "transmission": v.transmission,
            "branch": v.branch.name if v.branch else None,
            "vehicle_type": (v.vehicle_type.name
                             if getattr(v, "vehicle_type", None) else None),
        })
    return data


@bp.route("/my/vehicles", methods=["GET"])
@api_auth_required("vehicle.view")
def my_vehicles(api_user):
    """Vehicles currently assigned to the caller.

    An empty list for an unlinked user is the correct answer, not an
    error: most accounts are not assignees, and the app should render a
    clean "nothing assigned to you" rather than a failure screen.
    """
    ids = AssigneeScopeService().assigned_vehicle_ids(api_user)
    if not ids:
        return jsonify({"items": [], "total": 0})
    rows = Vehicle.query.filter(Vehicle.id.in_(ids)).order_by(
        Vehicle.plate_number).all()
    return jsonify({"items": [_row(v) for v in rows], "total": len(rows)})


@bp.route("/my/vehicles/<int:vid>", methods=["GET"])
@api_auth_required("vehicle.view")
def my_vehicle_detail(api_user, vid):
    v = _assigned_vehicle(vid, api_user)
    if v is None:
        return _not_found()
    return jsonify(_row(v, detail=True))


@bp.route("/my/vehicles/<int:vid>/odometer", methods=["POST"])
@api_auth_required("vehicle.update")
def my_vehicle_odometer(api_user, vid):
    """Post a reading for a vehicle the caller holds.

    Closes finding J2 for the mobile audience. The existing
    /vehicles/by-plate/odometer matches the plate against the org-scoped
    list, so any account with vehicle.update can post against any
    vehicle in its branch -- reasonable for a telematics integration
    account, not for a driver's phone.
    """
    v = _assigned_vehicle(vid, api_user)
    if v is None:
        return _not_found()

    p = request.get_json(silent=True) or {}
    raw = p.get("odometer")
    if raw is None or str(raw).strip() == "":
        return jsonify({"error": "validation_error",
                        "message": "Odometer reading is required.",
                        "field": "odometer"}), 400
    try:
        reading = int(raw)
    except (TypeError, ValueError):
        return jsonify({"error": "validation_error",
                        "message": "Odometer must be a whole number.",
                        "field": "odometer"}), 400
    if reading < 0:
        return jsonify({"error": "validation_error",
                        "message": "Odometer cannot be negative.",
                        "field": "odometer"}), 400
    # Readings only ever go up. A driver fat-fingering a digit should
    # not silently roll a vehicle's mileage backwards and, with it, its
    # PM due status.
    if v.current_odometer and reading < v.current_odometer:
        return jsonify({
            "error": "validation_error",
            "message": (f"Reading {reading} is lower than the last "
                        f"recorded {v.current_odometer}."),
            "field": "odometer"}), 400

    # Through OdometerService so the reading is LOGGED, not merely
    # applied. source=MOBILE is what makes the client's question --
    # "how do I check the update pushed from the phone" -- answerable at
    # all.
    from app.modules.master_data.vehicle.odometer_service import (
        OdometerError, OdometerService)
    try:
        OdometerService().record(v.id, reading, source="MOBILE",
                                 user=api_user)
    except OdometerError as e:
        return jsonify({"error": "validation_error",
                        "message": str(e), "field": "odometer"}), 400
    return jsonify(_row(v))


# ── documents (J4) and ATDs ──────────────────────────────────────────

@bp.route("/my/vehicles/<int:vid>/documents", methods=["GET"])
@api_auth_required("vehicle.view")
def my_vehicle_documents(api_user, vid):
    """Attachments (OR, CR, photos) for a vehicle the caller holds.

    Closes finding J4 for the mobile audience. The existing
    /attachments/* guard is well built -- it refuses attachments whose
    parent record is invisible -- but "visible" there means org scope,
    so any account with vehicle.view can download the OR and CR of any
    vehicle in its branch. The guard was never the problem; the scope
    underneath it was too wide for this audience.
    """
    if _assigned_vehicle(vid, api_user) is None:
        return _not_found()
    from app.core.attachments.attachment_service import AttachmentService
    rows = AttachmentService().list_for("vehicles", vid)
    return jsonify({"items": [{
        "id": a.id,
        "filename": a.original_filename or a.filename,
        "mime_type": a.mime_type,
        "file_size": a.file_size,
        "document_type": getattr(a, "document_type", None),
        "uploaded_at": a.created_at.isoformat() if a.created_at else None,
    } for a in rows], "total": len(rows)})


@bp.route("/my/vehicles/<int:vid>/documents/<int:aid>/download",
          methods=["GET"])
@api_auth_required("vehicle.view")
def my_vehicle_document_download(api_user, vid, aid):
    """Fetch one document's bytes.

    Assignment is re-checked here rather than trusted from the listing
    call. The two are separate requests and an attacker does not have to
    make the first one -- a guard that only runs on the path a
    well-behaved client happens to take is not a guard.

    The attachment is also checked to actually belong to THIS vehicle,
    or holding one vehicle would turn into an id-walk over every
    attachment in the system.
    """
    if _assigned_vehicle(vid, api_user) is None:
        return _not_found()

    from io import BytesIO
    from flask import send_file
    from app.core.models.attachment import Attachment
    from app.core.attachments.attachment_service import AttachmentService

    att = db.session.get(Attachment, aid)
    if (att is None or not att.is_active
            or att.reference_table != "vehicles"
            or att.reference_id != vid):
        return _not_found()

    # Bytes through the service, never att.file_data -- that is the
    # storage boundary, and a standing test enforces it.
    data = AttachmentService().get_bytes(att) or b""
    return send_file(
        BytesIO(data), as_attachment=True,
        download_name=att.original_filename or att.filename,
        mimetype=att.mime_type or "application/octet-stream")


@bp.route("/my/vehicles/<int:vid>/ongoing", methods=["GET"])
@api_auth_required("vehicle.view")
def my_vehicle_ongoing(api_user, vid):
    """Ongoing transactions on a vehicle the caller currently holds.

    What the client asked for on 2026-10-14: a driver opens the app and
    sees "what is in flight on my vehicle right now" -- any Maintenance
    Order, Trip Ticket, Movement or Authority To Drive that hasn't
    closed yet. One tap away from the vehicle page, so the driver's
    first screen answers "is anything happening to my unit".

    SCOPE: the usual /my/* rule -- 404 when the vehicle is not yours.
    No field here would leak less than the detail endpoint does, but
    the enumeration-oracle reasoning in the module docstring applies to
    every endpoint uniformly, not case by case.

    "Ongoing" follows the shared edit policy's open-document test: a
    document is listed while it is editable -- Draft, Pending,
    Returned, Approved, or in its own working status (MO In Progress,
    Trip Ticket Released, Vehicle Movement In Transit, ATD Active).
    Closed documents (Completed, Received, Cancelled, Expired) never
    appear. ATD specifically: a permit past its Valid To is finished
    whether or not the daily scan has stamped the status (KI-10), so
    the date is the real test.
    """
    from datetime import date as _date

    if _assigned_vehicle(vid, api_user) is None:
        return _not_found()

    items = []

    # Maintenance Orders -- the one every driver cares about: what is
    # being done to my unit?
    from app.modules.transactions.maintenance_order.models import (
        MaintenanceOrder)
    for mo in (MaintenanceOrder.query
               .filter(MaintenanceOrder.vehicle_id == vid,
                       MaintenanceOrder.status.in_(("DRAFT", "IN_PROGRESS")))
               .order_by(MaintenanceOrder.id.desc()).all()):
        items.append({
            "id": mo.id,
            "document_type": "Maintenance Order",
            "document_number": mo.document_number,
            "status": mo.status,
            # The description is what someone typed when they raised the
            # order -- "Brake pads", "PMS 10,000km" -- which is exactly
            # what a driver looking at this list wants to see.
            "summary": mo.description or (mo.transaction_type.name
                if getattr(mo, "transaction_type", None) else None),
            "date": (mo.scheduled_date.isoformat()
                     if mo.scheduled_date else None),
            "sort_date": mo.scheduled_date or _date.min,
        })

    from app.modules.transactions.trip_ticket.models import TripTicket
    for tt in (TripTicket.query
               .filter(TripTicket.vehicle_id == vid,
                       TripTicket.status.in_(("DRAFT", "RELEASED")))
               .order_by(TripTicket.id.desc()).all()):
        items.append({
            "id": tt.id,
            "document_type": "Trip Ticket",
            "document_number": tt.document_number,
            "status": tt.status,
            "summary": tt.destination or tt.purpose,
            "date": (tt.departure_datetime.date().isoformat()
                     if tt.departure_datetime else None),
            "sort_date": (tt.departure_datetime.date()
                          if tt.departure_datetime else _date.min),
        })

    from app.modules.transactions.vehicle_movement.models import VehicleMovement
    for mv in (VehicleMovement.query
               .filter(VehicleMovement.vehicle_id == vid,
                       VehicleMovement.status.in_(("DRAFT", "IN_TRANSIT")))
               .order_by(VehicleMovement.id.desc()).all()):
        items.append({
            "id": mv.id,
            "document_type": "Vehicle Movement",
            "document_number": mv.document_number,
            "status": mv.status,
            "summary": " -> ".join(filter(None, [mv.from_location,
                                                 mv.to_location])) or None,
            "date": (mv.movement_date.isoformat()
                     if mv.movement_date else None),
            "sort_date": mv.movement_date or _date.min,
        })

    from app.modules.transactions.atd.models import AuthorityToDrive
    from app.modules.transactions.atd.service import ATDService
    today = _date.today()
    for atd in (AuthorityToDrive.query
                .filter(AuthorityToDrive.vehicle_id == vid,
                        AuthorityToDrive.status.in_(("DRAFT", "ACTIVE")))
                .order_by(AuthorityToDrive.id.desc()).all()):
        # A permit past its Valid To is finished even if the stored
        # status still says ACTIVE (KI-10): the daily scan may be late,
        # and listing a lapsed authority to a driver would make the app
        # disagree with what the gate guard holds.
        if ATDService.is_expired(atd):
            continue
        items.append({
            "id": atd.id,
            "document_type": "Authority To Drive",
            "document_number": atd.document_number,
            "status": atd.status,
            "summary": atd.purpose,
            "date": atd.valid_from.isoformat() if atd.valid_from else None,
            "sort_date": atd.valid_from or _date.min,
        })

    # Newest first, by the document's own date -- the date on the row,
    # so a driver reading top-down starts with what is happening TODAY,
    # not with what was raised most recently in the system.
    items.sort(key=lambda i: i["sort_date"], reverse=True)
    for i in items:
        del i["sort_date"]
    return jsonify({"items": items, "total": len(items)})


@bp.route("/my/atds", methods=["GET"])
@api_auth_required("atd.view")
def my_atds(api_user):
    """ATDs issued TO the caller.

    Client decision, 2026-09-03: the ATD in the field app is for the
    signed-in assignee and nothing else. So the filter is the named
    DRIVER, not the assigned vehicle -- deliberately stricter. An ATD
    issued to somebody else stays invisible even while the caller
    currently holds that vehicle, because the ATD is a document about a
    person's authority, not about the vehicle.
    """
    from app.modules.transactions.atd.models import AuthorityToDrive

    assignee = AssigneeScopeService().assignee_for(api_user)
    if assignee is None:
        return jsonify({"items": [], "total": 0})
    rows = (AuthorityToDrive.query
            .filter_by(driver_id=assignee.id)
            .order_by(AuthorityToDrive.id.desc()).all())
    return jsonify({"items": [{
        "id": a.id,
        "document_number": a.document_number,
        "vehicle_id": a.vehicle_id,
        # The vehicle is named, not just referenced by id.
        #
        # An ATD is NOT filtered by whether the vehicle is assigned to
        # the holder -- a pool unit, a one-off delivery, or a temporary
        # authority while their own vehicle is in for maintenance all
        # produce an ATD for a vehicle they do not hold. The ATD IS the
        # authority, so it stands on its own.
        #
        # Which means the plate is essential rather than decorative: a
        # driver holding two authorities cannot otherwise tell them
        # apart, and an enforcer asking to see the authority for a
        # specific plate would be shown a document naming no plate.
        "plate_number": _vehicle_label(a.vehicle_id),
        "status": a.status,
        "valid_from": a.valid_from.isoformat() if a.valid_from else None,
        "valid_to": a.valid_to.isoformat() if a.valid_to else None,
        "purpose": a.purpose,
    } for a in rows], "total": len(rows)})


@bp.route("/my/atds/<int:aid>", methods=["GET"])
@api_auth_required("atd.view")
def my_atd_detail(api_user, aid):
    from app.modules.transactions.atd.models import AuthorityToDrive

    assignee = AssigneeScopeService().assignee_for(api_user)
    if assignee is None:
        return _not_found()
    atd = db.session.get(AuthorityToDrive, aid)
    if atd is None or atd.driver_id != assignee.id:
        return _not_found()
    return jsonify({
        "id": atd.id,
        "document_number": atd.document_number,
        "vehicle_id": atd.vehicle_id,
        "plate_number": _vehicle_label(atd.vehicle_id),
        "driver_id": atd.driver_id,
        "status": atd.status,
        "valid_from": (atd.valid_from.isoformat()
                       if atd.valid_from else None),
        "valid_to": atd.valid_to.isoformat() if atd.valid_to else None,
        "purpose": atd.purpose,
        "odometer_out": atd.odometer_out,
        "odometer_in": atd.odometer_in,
    })


@bp.route("/my/atds/<int:aid>/print", methods=["GET"])
@api_auth_required("atd.print")
def my_atd_print(api_user, aid):
    """The gate-guard slip, for the assignee it was issued to.

    Not a display of the record -- the PRINTOUT: the same payload
    /atd/<id>/print builds for the Jinja slip, so the phone renders the
    document a gate guard would be handed rather than a mobile-shaped
    summary of it.

    Scoped on the named driver, exactly like /my/atds. The general print
    endpoint uses org scope, which would let any driver print any
    branch-mate's authority to drive -- and an authority to drive is
    precisely the document where that matters, since it is what gets a
    vehicle through a gate.
    """
    from datetime import datetime
    from app.modules.api.atd import _atd_json, _atd_print_extras
    from app.modules.api.company_letterhead import company_letterhead
    from app.modules.transactions.atd.models import AuthorityToDrive

    assignee = AssigneeScopeService().assignee_for(api_user)
    if assignee is None:
        return _not_found()
    atd = db.session.get(AuthorityToDrive, aid)
    if atd is None or atd.driver_id != assignee.id:
        return _not_found()

    data = _atd_print_extras(atd, _atd_json(atd, detail=True),
                             api_user=api_user)

    # Only APPROVED levels become signature blocks, matching the Jinja
    # slip. A pending approver has not authorised anything, and printing
    # their name over a signature line would put an authorisation on
    # paper that nobody gave.
    signatures = [
        {"name": lvl.get("acted_by_name"),
         "level_number": lvl.get("level_number"),
         "acted_at": lvl.get("acted_at")}
        for lvl in (data.get("approval_chain") or [])
        if lvl.get("status") == "APPROVED" and lvl.get("acted_by_name")
    ]

    return jsonify({
        "atd": data,
        "signatures": signatures,
        "company": company_letterhead(),
        "generated_at": datetime.now().isoformat(),
        "printed_by": api_user.full_name or api_user.username,
        # Two copies: the gate guard keeps one, the driver keeps one.
        # Carried in the payload rather than hard-coded in the app so
        # the phone and the Jinja slip cannot disagree about it.
        "copies": 2,
    })


@bp.route("/my/vehicles/<int:vid>/odometer", methods=["GET"])
@api_auth_required("vehicle.view")
def my_vehicle_odometer_history(api_user, vid):
    """Recent readings for a vehicle the caller holds.

    So a driver can see what they submitted rather than guessing whether
    this morning's entry went through -- the mobile equivalent of the
    admin history.
    """
    from app.modules.master_data.vehicle.odometer_service import (
        OdometerService)

    if _assigned_vehicle(vid, api_user) is None:
        return _not_found()
    rows, total = OdometerService().history(vehicle_id=vid, per_page=30)
    return jsonify({"items": [{
        "id": e.id,
        "reading": e.reading,
        "previous_reading": e.previous_reading,
        "delta": e.delta,
        "source": e.source,
        "recorded_at": e.recorded_at.isoformat() if e.recorded_at else None,
        "recorded_by": e.user.full_name if e.user else None,
    } for e in rows], "total": total})
