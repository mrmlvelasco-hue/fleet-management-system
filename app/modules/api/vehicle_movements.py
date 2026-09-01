"""Vehicle Movement API.

Phase 1 scope in the project's master prompt, with zero API coverage
before this. Every rule stays in VehicleMovementService: the
MOVEMENT_TYPE validation, defaulting the driver from the vehicle's own
assignment, and the transit/complete transitions.

movement_type is validated against a configurable Lookup, never a
hardcoded list -- the master prompt's "All business rules must be
configurable through System Administration. No values shall be
hardcoded." The form-options endpoint below serves the SAME set the
service validates against, so a dropdown cannot drift from what will
actually be accepted.
"""
from flask import jsonify, request

from app.modules.api.auth import api_auth_required
from app.modules.api.coercion import Coercer, FieldValueError
from app.modules.api.routes import bp

_COERCER = Coercer(
    ints={"vehicle_id": "Vehicle", "driver_id": "Driver"},
    dates={"movement_date": "Movement Date"},
)


def _bad(message, field=None):
    return jsonify({"error": "validation", "message": message,
                    "fields": {field or "_": message}}), 400


def _not_found(label="Vehicle Movement"):
    return jsonify({"error": "not_found",
                    "message": f"{label} not found."}), 404


def _conflict(message):
    return jsonify({"error": "conflict", "message": message}), 409


def _parse_datetime(raw, label, field):
    """Movements record start and end TIMES, not just dates, so these
    are parsed here -- the shared Coercer handles ints/decimals/dates
    only, and widening it for one module's need would affect every
    other module that depends on it."""
    from datetime import datetime

    if raw in (None, ""):
        return None
    if isinstance(raw, datetime):
        return raw
    text = str(raw).strip().replace(" ", "T")
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise FieldValueError(f"{label} is not a valid date and time.", field)


def _iso(v):
    return v.isoformat() if v else None


def _mv_json(mv, *, detail=False):
    data = {
        "id": mv.id,
        "document_number": mv.document_number,
        "vehicle_id": mv.vehicle_id,
        "plate_number": (mv.vehicle.plate_number if mv.vehicle else None),
        "conduction_number": (mv.vehicle.conduction_number
                              if mv.vehicle else None),
        "vehicle_label": (
            " ".join(x for x in [getattr(mv.vehicle, "brand", None),
                                 getattr(mv.vehicle, "model", None)] if x)
            or None) if mv.vehicle else None,
        "driver_id": mv.driver_id,
        "driver": mv.driver.full_name if mv.driver else None,
        "employee_responsible": mv.employee_responsible,
        "purpose": mv.purpose,
        "movement_type": mv.movement_type,
        "from_location": mv.from_location,
        "to_location": mv.to_location,
        "movement_date": _iso(mv.movement_date),
        "movement_start_datetime": _iso(mv.movement_start_datetime),
        "movement_end_datetime": _iso(mv.movement_end_datetime),
        "remarks": mv.remarks,
        "status": mv.status,
        "requested_by": mv.requester.username if mv.requester else None,
    }
    if detail:
        from app.modules.api.company_letterhead import company_letterhead
        data["company"] = company_letterhead()
    return data


@bp.route("/vehicle-movements/form-options", methods=["GET"])
@api_auth_required("vehiclemovement.view")
def vehicle_movement_form_options(api_user):
    """The configured MOVEMENT_TYPE set, so the form renders exactly
    what the service will accept.

    Uses get_by_type_with_fallback -- the same helper Flask's own route
    uses -- so a fresh install where `flask seed all` has not run still
    shows the code-registered defaults rather than an empty dropdown.
    """
    from app.modules.system_admin.services.lookup_service import LookupService

    rows = LookupService().get_by_type_with_fallback("MOVEMENT_TYPE")
    return jsonify({"movement_types": [
        {"code": r.code, "description": r.description} for r in rows]})


@bp.route("/vehicle-movements", methods=["GET"])
@api_auth_required("vehiclemovement.view")
def list_vehicle_movements(api_user):
    from app.modules.transactions.vehicle_movement.service import (
        VehicleMovementService)

    page = request.args.get("page", 1, type=int)
    page_size = min(request.args.get("page_size", 25, type=int), 100)
    rows, pagination = VehicleMovementService().list_filtered(
        user=api_user, search=request.args.get("q") or None,
        status=request.args.get("status") or None,
        page=page, per_page=page_size)
    return jsonify({
        "items": [_mv_json(mv) for mv in rows],
        "total": pagination.total, "page": page, "page_size": page_size,
        "pages": pagination.pages,
    })


@bp.route("/vehicle-movements", methods=["POST"])
@api_auth_required("vehiclemovement.create")
def create_vehicle_movement(api_user):
    from app.modules.transactions.vehicle_movement.service import (
        InvalidMovementTypeError, VehicleMovementService)

    payload = request.get_json(silent=True) or {}
    try:
        fields = _COERCER.fields(payload, [
            "vehicle_id", "driver_id", "movement_date"])
        start_dt = _parse_datetime(payload.get("movement_start_datetime"),
                                   "Movement Start", "movement_start_datetime")
    except FieldValueError as exc:
        return _bad(str(exc), exc.field)

    if not fields.get("vehicle_id"):
        return _bad("Vehicle is required.", "vehicle_id")
    for required, label in (("from_location", "From Location"),
                            ("to_location", "To Location")):
        if not (payload.get(required) or "").strip():
            return _bad(f"{label} is required.", required)
    if not fields.get("movement_date"):
        return _bad("Movement Date is required.", "movement_date")
    if not payload.get("movement_type"):
        return _bad("Movement Type is required.", "movement_type")

    try:
        mv = VehicleMovementService().create(
            vehicle_id=fields["vehicle_id"],
            movement_type=payload["movement_type"],
            from_location=payload["from_location"],
            to_location=payload["to_location"],
            movement_date=fields["movement_date"], user=api_user,
            remarks=payload.get("remarks") or None,
            driver_id=fields.get("driver_id"),
            employee_responsible=payload.get("employee_responsible") or None,
            purpose=payload.get("purpose") or None,
            movement_start_datetime=start_dt)
    except InvalidMovementTypeError as exc:
        # The message names every valid code, because the set is
        # configurable and therefore not knowable from the code alone.
        return _bad(str(exc), "movement_type")
    except Exception as exc:
        return _conflict(str(exc))
    return jsonify(_mv_json(mv, detail=True)), 201


@bp.route("/vehicle-movements/<int:mid>", methods=["GET"])
@api_auth_required("vehiclemovement.view")
def vehicle_movement_detail(api_user, mid):
    from app.modules.transactions.vehicle_movement.models import (
        VehicleMovement)

    mv = VehicleMovement.query.filter_by(id=mid).first()
    if mv is None:
        return _not_found()
    return jsonify(_mv_json(mv, detail=True))


def _lifecycle(api_user, mid, method_name):
    from app.modules.transactions.vehicle_movement.models import (
        VehicleMovement)
    from app.modules.transactions.vehicle_movement.service import (
        VehicleMovementService)

    if VehicleMovement.query.filter_by(id=mid).first() is None:
        return _not_found()
    p = request.get_json(silent=True) or {}
    svc = VehicleMovementService()
    try:
        if method_name == "start_transit":
            svc.start_transit(mid)
        elif method_name == "submit":
            # See purchase_requests.py's _lifecycle_action: submit()
            # takes (record_id, user) only, and forwarding remarks
            # unconditionally raised "submit() got an unexpected
            # keyword argument 'remarks'" on every submit call.
            svc.submit(mid, user=api_user)
        else:
            getattr(svc, method_name)(mid, user=api_user,
                                      remarks=p.get("remarks"))
    except Exception as exc:
        return _conflict(str(exc))
    mv = VehicleMovement.query.filter_by(id=mid).first()
    return jsonify(_mv_json(mv, detail=True))


@bp.route("/vehicle-movements/<int:mid>/submit", methods=["POST"])
@api_auth_required("vehiclemovement.update")
def submit_vehicle_movement(api_user, mid):
    return _lifecycle(api_user, mid, "submit")


@bp.route("/vehicle-movements/<int:mid>/approve", methods=["POST"])
@api_auth_required("vehiclemovement.view")
def approve_vehicle_movement(api_user, mid):
    return _lifecycle(api_user, mid, "approve")


@bp.route("/vehicle-movements/<int:mid>/reject", methods=["POST"])
@api_auth_required("vehiclemovement.view")
def reject_vehicle_movement(api_user, mid):
    return _lifecycle(api_user, mid, "reject")


@bp.route("/vehicle-movements/<int:mid>/cancel", methods=["POST"])
@api_auth_required("vehiclemovement.update")
def cancel_vehicle_movement(api_user, mid):
    return _lifecycle(api_user, mid, "cancel")


@bp.route("/vehicle-movements/<int:mid>/start-transit", methods=["POST"])
@api_auth_required("vehiclemovement.update")
def start_transit_vehicle_movement(api_user, mid):
    return _lifecycle(api_user, mid, "start_transit")


@bp.route("/vehicle-movements/<int:mid>/complete", methods=["POST"])
@api_auth_required("vehiclemovement.update")
def complete_vehicle_movement(api_user, mid):
    """The end time is optional: a movement can be closed out after the
    fact without inventing a timestamp nobody recorded."""
    from app.modules.transactions.vehicle_movement.models import (
        VehicleMovement)
    from app.modules.transactions.vehicle_movement.service import (
        VehicleMovementService)

    if VehicleMovement.query.filter_by(id=mid).first() is None:
        return _not_found()

    payload = request.get_json(silent=True) or {}
    try:
        end_dt = _parse_datetime(payload.get("movement_end_datetime"),
                                 "Movement End", "movement_end_datetime")
    except FieldValueError as exc:
        return _bad(str(exc), exc.field)

    try:
        VehicleMovementService().complete(mid, movement_end_datetime=end_dt)
    except Exception as exc:
        return _conflict(str(exc))

    fresh = VehicleMovement.query.filter_by(id=mid).first()
    return jsonify(_mv_json(fresh, detail=True))
