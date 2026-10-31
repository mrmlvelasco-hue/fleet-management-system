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


def _atd_category_label(atd):
    category = getattr(atd, "vehicle_category", None)
    if category:
        from app.modules.system_admin.models import Lookup
        row = Lookup.query.filter_by(
            lookup_type="VEHICLE_CATEGORY", code=category).first()
        return row.description if row else category
    return atd.vehicle_type.name if atd.vehicle_type else None


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


# ── readiness: "Before you drive" ────────────────────────────────────

def _fmt_date(d):
    """'Nov 1' this year, 'Nov 1, 2027' otherwise -- the phone card is
    narrow, and the year only adds information when it differs."""
    from datetime import date as _date
    if d is None:
        return None
    label = f"{d.strftime('%b')} {d.day}"
    return label if d.year == _date.today().year else f"{label}, {d.year}"


def _days_phrase(days):
    if days == 0:
        return "today"
    if days == 1:
        return "1 day left"
    return f"{days} days left"


def _readiness_registration(vehicle):
    from app.modules.registration_config.service import (
        RegistrationDueCalculationService)
    try:
        s = RegistrationDueCalculationService().get_due_status(vehicle)
    except Exception:
        s = {}
    due = s.get("next_due_date")
    days = s.get("days_remaining")
    status = s.get("status")
    if due is None or days is None:
        return {"key": "registration", "label": "Registration",
                "state": "unknown", "date": None, "days_left": None,
                "detail": "No registration date on file"}
    if days < 0:
        state, detail = "alert", f"Expired {_fmt_date(due)}"
    elif status == "DUE_SOON":
        state, detail = "warn", f"Expires {_fmt_date(due)} — {_days_phrase(days)}"
    else:
        state, detail = "ok", f"Valid until {_fmt_date(due)}"
    return {"key": "registration", "label": "Registration", "state": state,
            "date": due.isoformat(), "days_left": days, "detail": detail}


# Covers a driver can be asked about, in the order they are named. The
# earliest to lapse decides the row: one lapsed cover is uninsured.
_INSURANCE_COVERS = (("CTPL", "ctpl_to_date"),
                     ("Comprehensive", "od_theft_aon_to_date"))
_INSURANCE_WARN_DAYS = 30


def _readiness_insurance(vehicle):
    from datetime import date as _date
    dated = [(name, getattr(vehicle, attr, None))
             for name, attr in _INSURANCE_COVERS]
    dated = [(n, d) for n, d in dated if d is not None]
    if not dated:
        return {"key": "insurance", "label": "Insurance", "state": "unknown",
                "date": None, "days_left": None,
                "detail": "No insurance dates on file"}
    name, end = min(dated, key=lambda nd: nd[1])
    days = (end - _date.today()).days
    if days < 0:
        state, detail = "alert", f"{name} lapsed {_fmt_date(end)}"
    elif days <= _INSURANCE_WARN_DAYS:
        state, detail = "warn", f"{name} ends {_fmt_date(end)} — {_days_phrase(days)}"
    else:
        names = " and ".join(n for n, _ in dated)
        state, detail = "ok", f"{names} valid until {_fmt_date(end)}"
    return {"key": "insurance", "label": "Insurance", "state": state,
            "date": end.isoformat(), "days_left": days, "detail": detail}


def _due_items(vehicle):
    """Every maintenance schedule on the unit, by name, most urgent first
    (PMDueCalculationService.vehicle_due_items). A failure there must not
    take the whole card down -- it degrades to "no schedule on file"."""
    from app.core.maintenance.due_calculation_service import (
        PMDueCalculationService)
    try:
        return PMDueCalculationService().vehicle_due_items(vehicle) or []
    except Exception:
        return []


def _pms_row(item, odo, key):
    status = item.get("status")
    due_km = item.get("next_due_km")
    due_date = item.get("next_due_date")
    if due_km is not None:
        gap = due_km - odo
        where = f"Due at {due_km:,} km"
        tail = (f"{gap:,} km to go" if gap >= 0
                else f"{abs(gap):,} km over")
        text = f"{where} — {tail}"
    elif due_date is not None:
        text = f"Due {_fmt_date(due_date)}"
    else:
        text = "Scheduled"
    state = {"OVERDUE": "alert", "DUE_SOON": "warn"}.get(status, "ok")
    if state == "ok" and due_km is not None:
        text = f"Next at {due_km:,} km — {due_km - odo:,} km to go"
    order = item.get("open_order")
    if order and order.get("document_number"):
        text = f"{text} · {order['document_number']} raised"
    return {"key": key,
            # WHICH maintenance (reported 2026-10-19: the row said only
            # "PMS", so a driver could not tell aircon servicing from a
            # preventive maintenance service).
            "label": item.get("maintenance_type") or "PMS",
            "state": state, "detail": text, "due_km": due_km,
            "due_date": due_date.isoformat() if due_date else None}


def _readiness_pms(vehicle):
    """The most urgent schedule as the "pms" row, named; any OTHER
    schedule that is also due soon or overdue gets a row of its own
    ("pms-<type id>"). Schedules that are fine stay off this card -- it
    answers "can I drive this today?"; the Home screen lists them all."""
    items = _due_items(vehicle)
    if not items:
        return [{"key": "pms", "label": "PMS", "state": "unknown",
                 "detail": "No PMS schedule set for this vehicle"}]
    odo = vehicle.current_odometer or 0
    rows = [_pms_row(items[0], odo, "pms")]
    for item in items[1:]:
        if item.get("status") in ("OVERDUE", "DUE_SOON"):
            rows.append(_pms_row(
                item, odo, f"pms-{item.get('maintenance_type_id')}"))
    return rows


def _readiness_work(vehicle_id):
    """Open Maintenance Orders, phrased as what they mean to the driver:
    in the workshop (the unit is not available) beats waiting for
    approval beats nothing at all."""
    from app.modules.transactions.maintenance_order.models import (
        MaintenanceOrder)
    open_orders = (MaintenanceOrder.query
                   .filter(MaintenanceOrder.vehicle_id == vehicle_id,
                           MaintenanceOrder.status.in_(("DRAFT", "IN_PROGRESS")))
                   .all())
    in_shop = [m for m in open_orders if m.status == "IN_PROGRESS"]
    if in_shop:
        m = in_shop[0]
        what = _mo_type_label(m) or m.document_number or "Maintenance"
        where = (f" at {m.vendor.name}" if getattr(m, "vendor", None) else "")
        return {"key": "work", "label": "Open work", "state": "alert",
                "detail": f"In the workshop{where} — {what}"}
    pending = [m for m in open_orders
               if getattr(m, "approval_instance", None) is not None
               and m.approval_instance.status in ("PENDING", "APPROVED",
                                                  "RETURNED")]
    if pending:
        n = len(pending)
        return {"key": "work", "label": "Open work", "state": "warn",
                "detail": (f"{n} order awaiting approval or scheduling"
                           if n == 1 else
                           f"{n} orders awaiting approval or scheduling")}
    return {"key": "work", "label": "Open work", "state": "ok",
            "detail": "Nothing in the shop"}


def _readiness_atd(vehicle_id, api_user):
    """The caller's own live ATD on this unit, or None. Not a required
    document for an assigned vehicle, so its absence is no row at all
    rather than a warning."""
    from app.modules.transactions.atd.models import AuthorityToDrive
    from app.modules.master_data.vehicle.models import Vehicle
    from app.modules.transactions.atd.service import ATDService
    assignee = AssigneeScopeService().assignee_for(api_user)
    if assignee is None:
        return None
    vehicle = db.session.get(Vehicle, vehicle_id)
    for atd in (AuthorityToDrive.query
                .filter_by(driver_id=assignee.id, status="ACTIVE")
                .order_by(AuthorityToDrive.valid_to.desc()).all()):
        if ATDService.authorizes_vehicle(atd, vehicle):
            return {"key": "atd", "label": "Authority To Drive",
                    "state": "ok",
                    "detail": (f"{atd.document_number or 'ATD'} active until "
                               f"{_fmt_date(atd.valid_to)}")}
    return None


def _assignment_summary(vehicle, api_user):
    from app.modules.master_data.vehicle.assignment_models import (
        VehicleAssignment)
    assignee = AssigneeScopeService().assignee_for(api_user)
    row = None
    if assignee is not None:
        row = (VehicleAssignment.query
               .filter_by(vehicle_id=vehicle.id, driver_id=assignee.id,
                          assigned_to=None)
               .order_by(VehicleAssignment.id.desc()).first())
    since = getattr(row, "assigned_from", None) if row else None
    if since is None and row is not None and getattr(row, "created_at", None):
        since = row.created_at.date()
    return {
        "since": since.isoformat() if since else None,
        "branch": vehicle.branch.name if vehicle.branch else None,
        "source": getattr(row, "source", None) if row else None,
    }


@bp.route("/my/vehicles/<int:vid>/readiness", methods=["GET"])
@api_auth_required("vehicle.view")
def my_vehicle_readiness(api_user, vid):
    """"Before you drive" -- the checklist that opens My Vehicle.

    Direction C of the My Vehicle mockups (Michael, 2026-10-14): one card
    that answers "can I drive this today?" -- Registration, Insurance,
    PMS and open work, plus the driver's own live ATD when they hold one.

    Each fact comes from the service the desktop already uses for it
    (RegistrationDueCalculationService, PMDueCalculationService, the
    vehicle's insurance cover dates, the open Maintenance Orders,
    ATDService.is_expired), so the phone can never disagree with the
    Fleet Office about whether a unit is due.

    state: ok | warn | alert | unknown. `attention_count` counts warn
    and alert only: "unknown" means master data is missing, which the
    driver can neither see the cause of nor fix, so it is shown but
    never makes the headline say there is something to check.

    SCOPE: the usual /my/* rule -- 404 when the vehicle is not yours.
    """
    v = _assigned_vehicle(vid, api_user)
    if v is None:
        return _not_found()
    checks = [_readiness_registration(v), _readiness_insurance(v),
              *_readiness_pms(v), _readiness_work(v.id)]
    atd = _readiness_atd(v.id, api_user)
    if atd is not None:
        checks.append(atd)
    attention = sum(1 for c in checks if c["state"] in ("warn", "alert"))
    return jsonify({"checks": checks, "attention_count": attention,
                    "assignment": _assignment_summary(v, api_user)})


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
            # When it went in for approval -- the Maintenance tab shows
            # how long it has been waiting, which is the number a driver
            # chasing an approval actually wants.
            "submitted_at": (inst.created_at.isoformat()
                             if inst is not None
                             and getattr(inst, "created_at", None) else None),
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
        "authorization_type": getattr(a, "authorization_type", None) or "SPECIFIC_VEHICLE",
        "vehicle_type_id": a.vehicle_type_id,
        "vehicle_category": a.vehicle_category,
        "vehicle_type_name": _atd_category_label(a),
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
        "authorization_type": getattr(atd, "authorization_type", None) or "SPECIFIC_VEHICLE",
        "vehicle_type_id": atd.vehicle_type_id,
        "vehicle_category": atd.vehicle_category,
        "vehicle_type_name": _atd_category_label(atd),
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


# ── Home: "Coming up for <plate>" (mockup 2, 2026-10-15) ─────────────

_COMING_UP_RANK = {"overdue": 0, "in_shop": 1, "awaiting": 2,
                   "due_soon": 3, "ok": 4}


def _interval_phrase(km, days):
    bits = []
    if km:
        bits.append(f"{km:,} km")
    if days:
        if days >= 60 and days % 30 == 0:
            months = days // 30
            bits.append(f"{months} months")
        else:
            bits.append(f"{days} days")
    return f"Every {' or '.join(bits)}" if bits else None


def _gap_phrase(item, odo, today):
    """("in 680 km" | "100 km over" | "in 41 days" | "3 days over", over?)"""
    km, d = item.get("next_due_km"), item.get("next_due_date")
    if km is not None:
        gap = km - odo
        return (f"in {gap:,} km", False) if gap >= 0 else (
            f"{abs(gap):,} km over", True)
    if d is not None:
        days = (d - today).days
        if days == 0:
            return "today", False
        if days > 0:
            return (f"in {days} day" if days == 1
                    else f"in {days} days"), False
        return (f"{abs(days)} day over" if days == -1
                else f"{abs(days)} days over"), True
    return None, False


def _waited(inst):
    from datetime import datetime as _dt
    created = getattr(inst, "created_at", None) if inst else None
    if created is None:
        return None
    days = (_dt.utcnow().date() - created.date()).days
    return "today" if days <= 0 else ("1 day" if days == 1
                                       else f"{days} days")


def _pms_coming_up(item, odo, today):
    from app.modules.transactions.maintenance_order.models import (
        MaintenanceOrder)
    title = item.get("maintenance_type") or "PMS"
    key = f"pms-{item.get('maintenance_type_id')}"
    order_ref = item.get("open_order")
    mo = (db.session.get(MaintenanceOrder, order_ref["id"])
          if order_ref and order_ref.get("id") else None)
    if mo is not None:
        number = mo.document_number or f"MO #{mo.id}"
        if mo.status == "IN_PROGRESS":
            where = (f" at {mo.vendor.name}"
                     if getattr(mo, "vendor", None) else "")
            return {"key": key, "kind": "pms", "title": title,
                    "detail": f"{number} · In the workshop{where}",
                    "state": "in_shop", "badge": "In the workshop",
                    "order_id": mo.id}
        inst = getattr(mo, "approval_instance", None)
        bits = [number]
        with_whom = _mo_pending_with(mo)
        if with_whom:
            bits.append(f"With {with_whom}")
        elif mo.status == "DRAFT" and inst is None:
            bits.append("Draft — not yet submitted")
        waited = _waited(inst)
        if waited and with_whom:
            bits.append(waited)
        return {"key": key, "kind": "pms", "title": title,
                "detail": " · ".join(bits), "state": "awaiting",
                "badge": "Awaiting", "order_id": mo.id}

    gap, _over = _gap_phrase(item, odo, today)
    status = item.get("status")
    state = {"OVERDUE": "overdue", "DUE_SOON": "due_soon"}.get(status, "ok")
    last_km, last_date = item.get("last_km"), item.get("last_date")
    if last_km:
        last = f"last at {last_km:,} km"
    elif last_date:
        last = f"last {_fmt_date(last_date)}"
    else:
        last = "no service on record yet"
    detail = " · ".join(x for x in (
        _interval_phrase(item.get("interval_km"),
                         item.get("interval_days")), last) if x)
    return {"key": key, "kind": "pms", "title": title, "detail": detail,
            "state": state,
            "badge": "Overdue" if state == "overdue" else (gap or "Scheduled"),
            "order_id": None}


def _registration_coming_up(vehicle, today):
    from app.modules.registration_config.service import (
        RegistrationDueCalculationService)
    try:
        s = RegistrationDueCalculationService().get_due_status(vehicle)
    except Exception:
        s = {}
    due, days = s.get("next_due_date"), s.get("days_remaining")
    if due is None or days is None:
        return None
    plate = (vehicle.plate_number or "").strip()
    digits = [c for c in plate if c.isdigit()]
    detail = (f"LTO · plate ending {digits[-1]}" if digits else "LTO")
    if days < 0:
        state, badge = "overdue", "Expired"
    elif s.get("status") == "DUE_SOON":
        state, badge = "due_soon", (f"in {days} day" if days == 1
                                    else f"in {days} days")
    else:
        state, badge = "ok", f"{due.strftime('%b')} {due.year}"
    return {"key": "registration", "kind": "registration",
            "title": "Registration renewal", "detail": detail,
            "state": state, "badge": badge, "order_id": None}


@bp.route("/my/vehicles/<int:vid>/coming-up", methods=["GET"])
@api_auth_required("vehicle.view")
def my_vehicle_coming_up(api_user, vid):
    """The Home screen's "Coming up for <plate>" list (mockup 2).

    One row per maintenance schedule on the unit, NAMED -- Preventive
    Maintenance Service, Aircon Servicing, Tire Rotation -- from the same
    per-type due calculation the dashboard uses, so the phone and the
    Fleet Office never disagree about what is due. A schedule that
    already has an order raised shows as that order (number, who it is
    with, how long), because that is what the driver is waiting on.
    Then the registration renewal.

    `next_pms` is the most urgent schedule, for the vehicle card.

    SCOPE: the usual /my/* rule -- 404 when the vehicle is not yours.
    """
    from datetime import date as _date
    v = _assigned_vehicle(vid, api_user)
    if v is None:
        return _not_found()
    today = _date.today()
    odo = v.current_odometer or 0
    due = _due_items(v)
    items = [_pms_coming_up(i, odo, today) for i in due]
    reg = _registration_coming_up(v, today)
    if reg is not None:
        items.append(reg)
    items.sort(key=lambda i: _COMING_UP_RANK.get(i["state"], 9))

    next_pms = None
    if due:
        first = due[0]
        text, over = _gap_phrase(first, odo, today)
        next_pms = {"title": first.get("maintenance_type") or "PMS",
                    "text": text, "over": over,
                    "status": first.get("status")}
    return jsonify({"items": items, "next_pms": next_pms})


# ── Tires & battery of my vehicle (mockup 3, 2026-10-15) ─────────────

# Unmeasured (often the spare) after the measured ones: the list leads
# with what needs looking at.
_CONDITION_RANK = {"REPLACE": 0, "WATCH": 1, "WEAK": 1, "GOOD": 3, None: 4}


def _add_months(d, months):
    import calendar
    m = d.month - 1 + months
    y, m = d.year + m // 12, m % 12 + 1
    return d.replace(year=y, month=m,
                     day=min(d.day, calendar.monthrange(y, m)[1]))


@bp.route("/my/vehicles/<int:vid>/equipment", methods=["GET"])
@api_auth_required("vehicle.view")
def my_vehicle_equipment(api_user, vid):
    """The tires on each wheel and the battery, for the assignee.

    Read-only: something wrong goes to the fleet supervisor, who raises
    the transaction so it goes through approval (client decision 3,
    2026-10-15). Conditions use the same System Parameter thresholds as
    the Maintenance Order's Tire & Battery Work, which are sent along so
    the legend on the phone states the real numbers.

    vehicle.view rather than tire.view: a driver holds no master-data
    permission, and the /my/* scope already limits this to their unit.
    """
    from app.modules.master_data.battery.models import Battery
    from app.modules.master_data.tire.models import Tire
    from app.modules.transactions.maintenance_order.equipment import (
        EquipmentConditionService, EquipmentWorkService)
    from app.modules.transactions.tire_txn.models import TireTransaction

    v = _assigned_vehicle(vid, api_user)
    if v is None:
        return _not_found()
    cond = EquipmentConditionService()
    order = EquipmentWorkService()._position_order()
    odo = v.current_odometer

    tires = []
    for t in Tire.query.filter_by(vehicle_id=vid, status="MOUNTED").all():
        km_on = (odo - t.mounted_odometer
                 if odo is not None and t.mounted_odometer is not None
                 and odo >= t.mounted_odometer else None)
        tires.append({
            "id": t.id, "position": t.wheel_position,
            "serial_number": t.serial_number, "brand": t.brand,
            "model": t.model, "size": t.size,
            "label": " ".join(x for x in (t.brand, t.model) if x),
            "tread_mm": (float(t.current_tread_mm)
                         if t.current_tread_mm is not None else None),
            "condition": cond.tire(t), "km_on_it": km_on,
            "mounted_date": (t.mounted_date.isoformat()
                             if t.mounted_date else None),
        })
    tires.sort(key=lambda t: (_CONDITION_RANK.get(t["condition"], 2),
                              order.get(t["position"] or "", 999)))

    battery = None
    b = (Battery.query.filter_by(vehicle_id=vid, status="MOUNTED")
         .order_by(Battery.id.desc()).first())
    if b is not None:
        spec = " · ".join(x for x in (
            f"{b.voltage} V" if b.voltage else None,
            f"{b.capacity_ah} Ah" if b.capacity_ah else None) if x)
        name = " ".join(x for x in (b.brand, b.model) if x)
        battery = {
            "id": b.id, "serial_number": b.serial_number,
            "label": " · ".join(x for x in (name, spec) if x),
            "installed": (b.mounted_date.isoformat()
                          if b.mounted_date else None),
            "last_voltage": (float(b.last_voltage)
                             if b.last_voltage is not None else None),
            "last_test_date": (b.last_test_date.isoformat()
                               if b.last_test_date else None),
            "warranty_to": (_add_months(b.mounted_date,
                                        b.warranty_months).isoformat()
                            if b.mounted_date and b.warranty_months
                            else None),
            "condition": cond.battery(b),
        }

    last = (TireTransaction.query
            .filter(TireTransaction.vehicle_id == vid,
                    TireTransaction.status == "COMPLETED",
                    TireTransaction.tread_depth_mm.isnot(None))
            .order_by(TireTransaction.transaction_date.desc(),
                      TireTransaction.id.desc()).first())
    last_inspection = None
    if last is not None:
        mo = getattr(last, "maintenance_order", None)
        last_inspection = {
            "date": (last.transaction_date.isoformat()
                     if last.transaction_date else None),
            "document": (mo.document_number if mo is not None
                         else last.document_number)}

    return jsonify({"tires": tires, "battery": battery,
                    "last_inspection": last_inspection,
                    "thresholds": cond.thresholds()})
