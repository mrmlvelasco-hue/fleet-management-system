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


def _mo_pending_with(mo):
    """Role or user label for whoever the approval is currently with.

    Reads the SAME approval chain the desktop detail renders in its
    workflow panel (ApprovalEngine.get_approval_chain), so the mobile
    says "Pending: Branch Manager" with the same wording the approver
    sees on their own screen -- no second source of truth for the
    label. None when the instance is not PENDING (nothing to pend)."""
    inst = getattr(mo, "approval_instance", None)
    if inst is None or inst.status != "PENDING":
        return None
    try:
        from app.core.approval.engine import ApprovalEngine
        for entry in ApprovalEngine().get_approval_chain(inst):
            if entry.get("status") == "CURRENT":
                # The chain's own labels prefix "Role: " / "User: " for
                # the desktop sidebar's two-line layout. The mobile card
                # is short and already says "Pending:" alongside, so the
                # prefix would read as "Pending: Role: Branch Manager".
                label = entry.get("approver_label") or ""
                for prefix in ("Role: ", "User: "):
                    if label.startswith(prefix):
                        return label[len(prefix):]
                return label or None
    except Exception:
        # The chain is a nicety here, not the authority to act. Never
        # fail the whole response because the engine stumbled on one
        # row -- the status still comes back, just without a name.
        pass
    return None


def _mo_type_label(mo):
    """What this is -- "Preventive Maintenance", "Corrective — Brake
    System", a transaction-type name, or the raw description. Picks the
    richest value available; the UI shows this as the row's title."""
    base = (mo.maintenance_type.name
            if getattr(mo, "maintenance_type", None) else None)
    if base is None and getattr(mo, "transaction_type", None):
        base = mo.transaction_type.name
    # The description adds a specific like "40,000 km" or "Brake System".
    description = (mo.description or "").strip()
    if base and description and description != base:
        return f"{base} — {description}"
    return base or description or None


def _pr_display_status(pr):
    """Mirrors the Purchase Request list labels -- Draft / Pending /
    Approved / Rejected / Ordered / Received / Cancelled."""
    phys = (pr.status or "").upper()
    labels = {"DRAFT": "Draft", "PENDING": "Pending", "APPROVED": "Approved",
              "REJECTED": "Rejected", "ORDERED": "Ordered",
              "RECEIVED": "Received", "CANCELLED": "Cancelled"}
    return labels.get(phys, phys.title() if phys else "Draft")


_CLOSED_MO_STATUSES = ("COMPLETED", "CANCELLED")


@bp.route("/my/vehicles/<int:vid>/maintenance-orders", methods=["GET"])
@api_auth_required("vehicle.view")
def my_vehicle_maintenance_orders(api_user, vid):
    """Maintenance Orders on a vehicle the caller currently holds.

    Backs the My Vehicle screen's "Maintenance Orders" card (Michael's
    mockup, 2026-10-14): one row per MO on this unit, carrying the SAME
    `status` / `display_status` / `approval_status` triplet the desktop
    MO list serializes (_display_status in maintenance_orders.py), plus
    `pending_with` (current approver's role or user label) and any
    linked Purchase Request nested underneath. A specific endpoint, not
    a slice of a generic one -- reusing the display rule is the whole
    point, so two surfaces can never disagree about what "For Approval"
    means on the same order.

    SCOPE: the usual /my/* rule -- 404 when the vehicle is not yours.

    All MOs on this vehicle come back, including closed ones (Completed,
    Cancelled). The panel decides how many to show (the mockup folds
    them under "View all maintenance history"); the endpoint hands over
    what's there. In-flight rows come first, so the top of the card is
    the two or three orders a driver actually needs to see today.
    """
    from datetime import date as _date

    if _assigned_vehicle(vid, api_user) is None:
        return _not_found()

    from app.modules.transactions.maintenance_order.models import (
        MaintenanceOrder)
    # The display-status helper is on the desktop MO module; importing
    # here keeps that module from becoming an import-time dependency of
    # /my/* and avoids a cycle through blueprints.
    from app.modules.api.maintenance_orders import _display_status

    orders = (MaintenanceOrder.query
              .filter(MaintenanceOrder.vehicle_id == vid)
              .order_by(MaintenanceOrder.id.desc()).all())

    items = []
    for mo in orders:
        inst = getattr(mo, "approval_instance", None)
        pr = getattr(mo, "purchase_request", None)
        items.append({
            "id": mo.id,
            "document_number": mo.document_number,
            "status": mo.status,
            # The user-facing label the desktop list uses -- "Draft",
            # "Awaiting Approval", "Approved", "In Progress",
            # "Completed", "Cancelled", "Returned for Revision",
            # "Rejected". Single source of truth, _display_status.
            "display_status": _display_status(mo),
            "approval_status": inst.status if inst is not None else None,
            # Who is holding this up right now, when it is in approval.
            "pending_with": _mo_pending_with(mo),
            "type_label": _mo_type_label(mo),
            "category": getattr(mo, "order_category", None),
            "scheduled_date": (mo.scheduled_date.isoformat()
                               if mo.scheduled_date else None),
            "completed_date": (mo.completed_date.isoformat()
                               if mo.completed_date else None),
            "vendor": mo.vendor.name if getattr(mo, "vendor", None) else None,
            "purchase_request": ({
                "id": pr.id,
                "document_number": pr.document_number,
                "status": pr.status,
                "display_status": _pr_display_status(pr),
            } if pr is not None else None),
            # Sort key, stripped before responding.
            "_closed": (mo.status or "").upper() in _CLOSED_MO_STATUSES,
            "_sort": mo.scheduled_date or mo.completed_date or _date.min,
        })

    # In-flight first (so the mockup's "2 active" sit at the top), then
    # closed. Within each bucket, newest by date first.
    items.sort(key=lambda i: (i["_closed"], -i["_sort"].toordinal()
                              if i["_sort"] != _date.min else 0))
    active_count = sum(1 for i in items if not i["_closed"])
    for i in items:
        del i["_closed"]
        del i["_sort"]
    return jsonify({"items": items, "total": len(items),
                    "active_count": active_count})


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
