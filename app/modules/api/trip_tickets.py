"""Trip Ticket API.

Phase 1 scope in the project's master prompt, with zero API coverage
before this. Every rule stays in TripTicketService -- numbering, the
REQUIRE_DRIVER_FROM_MASTER decision, the release gate, and the
non-regressing odometer update on completion -- so this layer
validates, coerces and serialises, and computes nothing of its own.
"""
from flask import jsonify, request

from app.modules.api.auth import api_auth_required
from app.modules.api.coercion import Coercer, FieldValueError
from app.modules.api.routes import bp

_COERCER = Coercer(
    ints={"vehicle_id": "Vehicle", "driver_id": "Driver",
          "odometer_out": "Odometer Out"},
)


def _parse_datetime(raw, label, field):
    """Trip tickets need a real datetime, not a date: departure and
    return TIMES are the point of the document. The shared Coercer
    handles ints/decimals/dates only, so datetimes are parsed here
    rather than widening a helper every other module depends on.

    Accepts the ISO forms an <input type="datetime-local"> produces
    ("2026-08-27T08:30", with or without seconds).
    """
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


def _bad(message, field=None):
    return jsonify({"error": "validation", "message": message,
                    "fields": {field or "_": message}}), 400


def _not_found(label="Trip Ticket"):
    return jsonify({"error": "not_found",
                    "message": f"{label} not found."}), 404


def _conflict(message):
    return jsonify({"error": "conflict", "message": message}), 409


def _iso(v):
    return v.isoformat() if v else None


def _trip_json(t, *, detail=False):
    """`driver` is the DISPLAY name from whichever source applies --
    the master record when one is linked, the typed name otherwise.
    The client should not have to know which mode the system is in to
    render a driver's name."""
    driver_label = None
    if t.driver is not None:
        driver_label = t.driver.full_name
    elif t.driver_name_manual:
        driver_label = t.driver_name_manual

    data = {
        "id": t.id,
        "document_number": t.document_number,
        "vehicle_id": t.vehicle_id,
        "plate_number": t.vehicle.plate_number if t.vehicle else None,
        "vehicle_label": (
            " ".join(x for x in [getattr(t.vehicle, "brand", None),
                                 getattr(t.vehicle, "model", None)] if x)
            or None) if t.vehicle else None,
        "driver": driver_label,
        "driver_id": t.driver_id,
        "driver_name_manual": t.driver_name_manual,
        "destination": t.destination,
        "purpose": t.purpose,
        "departure_datetime": _iso(t.departure_datetime),
        "return_datetime": _iso(t.return_datetime),
        "odometer_out": t.odometer_out,
        "odometer_in": t.odometer_in,
        "passengers": t.passengers,
        "status": t.status,
        "requested_by": t.requester.username if t.requester else None,
    }
    if detail:
        from app.modules.api.company_letterhead import company_letterhead
        data["company"] = company_letterhead()
    return data


@bp.route("/trip-tickets", methods=["GET"])
@api_auth_required("tripticket.view")
def list_trip_tickets(api_user):
    from app.modules.transactions.trip_ticket.service import TripTicketService

    page = request.args.get("page", 1, type=int)
    page_size = min(request.args.get("page_size", 25, type=int), 100)
    rows, pagination = TripTicketService().list_filtered(
        user=api_user, search=request.args.get("q") or None,
        status=request.args.get("status") or None,
        page=page, per_page=page_size)
    return jsonify({
        "items": [_trip_json(t) for t in rows],
        "total": pagination.total, "page": page, "page_size": page_size,
        "pages": pagination.pages,
    })


@bp.route("/trip-tickets", methods=["POST"])
@api_auth_required("tripticket.create")
def create_trip_ticket(api_user):
    from app.modules.transactions.trip_ticket.service import (
        DriverRequiredError, TripTicketService)

    payload = request.get_json(silent=True) or {}
    try:
        fields = _COERCER.fields(payload, [
            "vehicle_id", "driver_id", "odometer_out"])
        fields["departure_datetime"] = _parse_datetime(
            payload.get("departure_datetime"), "Departure Date/Time",
            "departure_datetime")
    except FieldValueError as exc:
        return _bad(str(exc), exc.field)

    for required in ("destination", "purpose"):
        if not payload.get(required):
            return _bad(f"{required.title()} is required.", required)
    if not fields.get("vehicle_id"):
        return _bad("Vehicle is required.", "vehicle_id")
    if not fields.get("departure_datetime"):
        return _bad("Departure Date/Time is required.", "departure_datetime")

    driver_name_manual = (payload.get("driver_name_manual") or "").strip()
    # Manual mode means TYPED, not absent. A trip ticket with neither a
    # master driver nor a name records no one accountable for the
    # vehicle, which is worse than either configured mode intends.
    # TripTicketService enforces the master-required half of this rule;
    # the "manual mode still needs a name" half has no service-side
    # guard, so it is enforced here rather than silently allowed.
    if not fields.get("driver_id") and not driver_name_manual:
        return _bad("A driver must be selected or named.", "driver_id")

    try:
        trip = TripTicketService().create(
            vehicle_id=fields["vehicle_id"],
            destination=payload["destination"], purpose=payload["purpose"],
            departure_datetime=fields["departure_datetime"],
            odometer_out=fields.get("odometer_out"), user=api_user,
            driver_id=fields.get("driver_id"),
            driver_name_manual=driver_name_manual or None,
            passengers=payload.get("passengers") or None)
    except DriverRequiredError as exc:
        return _bad(str(exc), "driver_id")
    except Exception as exc:
        return _conflict(str(exc))
    return jsonify(_trip_json(trip, detail=True)), 201


@bp.route("/trip-tickets/<int:tid>", methods=["GET"])
@api_auth_required("tripticket.view")
def trip_ticket_detail(api_user, tid):
    from app.modules.transactions.trip_ticket.models import TripTicket

    trip = TripTicket.query.filter_by(id=tid).first()
    if trip is None:
        return _not_found()
    return jsonify(_trip_json(trip, detail=True))


def _lifecycle(api_user, tid, method_name):
    from app.modules.transactions.trip_ticket.models import TripTicket
    from app.modules.transactions.trip_ticket.service import (
        InvalidTripStateError, TripTicketService)

    if TripTicket.query.filter_by(id=tid).first() is None:
        return _not_found()
    p = request.get_json(silent=True) or {}
    svc = TripTicketService()
    try:
        if method_name == "release":
            svc.release(tid)
        else:
            getattr(svc, method_name)(tid, user=api_user,
                                      remarks=p.get("remarks"))
    except InvalidTripStateError as exc:
        return _conflict(str(exc))
    except Exception as exc:
        return _conflict(str(exc))
    trip = TripTicket.query.filter_by(id=tid).first()
    return jsonify(_trip_json(trip, detail=True))


@bp.route("/trip-tickets/<int:tid>/submit", methods=["POST"])
@api_auth_required("tripticket.update")
def submit_trip_ticket(api_user, tid):
    return _lifecycle(api_user, tid, "submit")


@bp.route("/trip-tickets/<int:tid>/approve", methods=["POST"])
@api_auth_required("tripticket.view")
def approve_trip_ticket(api_user, tid):
    return _lifecycle(api_user, tid, "approve")


@bp.route("/trip-tickets/<int:tid>/reject", methods=["POST"])
@api_auth_required("tripticket.view")
def reject_trip_ticket(api_user, tid):
    return _lifecycle(api_user, tid, "reject")


@bp.route("/trip-tickets/<int:tid>/cancel", methods=["POST"])
@api_auth_required("tripticket.update")
def cancel_trip_ticket(api_user, tid):
    return _lifecycle(api_user, tid, "cancel")


@bp.route("/trip-tickets/<int:tid>/release", methods=["POST"])
@api_auth_required("tripticket.update")
def release_trip_ticket(api_user, tid):
    """Gated by the service on the approval instance being APPROVED --
    a vehicle must not leave the yard on an undecided request."""
    return _lifecycle(api_user, tid, "release")


@bp.route("/trip-tickets/<int:tid>/complete", methods=["POST"])
@api_auth_required("tripticket.update")
def complete_trip_ticket(api_user, tid):
    """Records the return and pushes the closing odometer onto Vehicle
    Master -- but only forward, per the service's own guard."""
    from app.modules.transactions.trip_ticket.models import TripTicket
    from app.modules.transactions.trip_ticket.service import TripTicketService

    trip = TripTicket.query.filter_by(id=tid).first()
    if trip is None:
        return _not_found()

    payload = request.get_json(silent=True) or {}
    try:
        fields = Coercer(
            ints={"odometer_in": "Odometer In"},
        ).fields(payload, ["odometer_in"])
        fields["return_datetime"] = _parse_datetime(
            payload.get("return_datetime"), "Return Date/Time",
            "return_datetime")
    except FieldValueError as exc:
        return _bad(str(exc), exc.field)

    if fields.get("odometer_in") is None:
        return _bad("Odometer In is required to complete a trip.",
                    "odometer_in")

    try:
        TripTicketService().complete(
            tid, odometer_in=fields["odometer_in"],
            return_datetime=fields.get("return_datetime"))
    except Exception as exc:
        return _conflict(str(exc))

    fresh = TripTicket.query.filter_by(id=tid).first()
    return jsonify(_trip_json(fresh, detail=True))
