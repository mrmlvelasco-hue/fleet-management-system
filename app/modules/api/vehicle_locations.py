"""Rescue Map / vehicle location endpoints, for the FMS Field app.

One scope rule per endpoint, readable at a glance (the convention set
in my_vehicles.py):

  /my/vehicles/<id>/locations         the vehicle must be CURRENTLY
  /my/vehicles/<id>/tracking/start      assigned to the caller; 404
  /my/vehicles/<id>/tracking/stop       otherwise
                                      permission: vehiclelocation.create

  /vehicles/<id>/locations            MANUAL capture of a vehicle the
                                      caller's org scope covers -- the
                                      Fleet Coordinator case.
                                      permission: .view AND .create

  /rescue-map/vehicles                .view: every vehicle the caller's
  /vehicles/<id>/location             org scope covers (and a user
  /vehicles/<id>/location-history     restricted to assigned vehicles
                                      stays restricted). Without .view,
                                      only the caller's assigned
                                      vehicles. Neither permission: 403.

404, not 403, whenever a VEHICLE is out of reach: a location answers
"where is this vehicle", and a 403 would confirm the vehicle exists.
The reporting user is always the token's user, never a payload field.
"""
from flask import jsonify, request
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.modules.api.auth import api_auth_required
from app.modules.api.routes import bp
from app.modules.master_data.vehicle.models import Vehicle
from app.modules.user_management.assignee_scope_service import (
    AssigneeScopeService)
from app.modules.vehicle_location.service import (
    MAP_VEHICLE_LIMIT, MAX_POINTS_PER_REQUEST, SOURCES, STATUSES, PointError,
    VehicleLocationService, parse_time)

VIEW = "vehiclelocation.view"
CREATE = "vehiclelocation.create"


def _not_found():
    return jsonify({"error": "not_found",
                    "message": "Vehicle not found."}), 404


def _forbidden(message):
    return jsonify({"error": "forbidden", "message": message}), 403


def _bad(message):
    return jsonify({"error": "validation", "message": message}), 400


def _can_view_fleet(user):
    return user.has_permission(VIEW)


def _assigned_vehicle(vehicle_id, user):
    if not AssigneeScopeService().covers_vehicle(user, vehicle_id):
        return None
    return db.session.get(Vehicle, vehicle_id)


def _readable_vehicle(vehicle_id, user):
    """The map/detail/history rule. None = out of reach (answer 404)."""
    if _can_view_fleet(user):
        from app.modules.master_data.vehicle.service import VehicleService
        vehicle = VehicleService().get_visible(vehicle_id, user)
        if vehicle is not None:
            return vehicle
    return _assigned_vehicle(vehicle_id, user)


def _points_from_request():
    body = request.get_json(silent=True) or {}
    points = body.get("points")
    if not isinstance(points, list) or not points:
        return None, _bad("Send a non-empty list in 'points'.")
    if len(points) > MAX_POINTS_PER_REQUEST:
        return None, _bad(f"Send at most {MAX_POINTS_PER_REQUEST} points "
                          f"per request.")
    return points, None


def _ingest(vehicle, user, points, allow_automatic):
    try:
        result = VehicleLocationService().ingest(
            vehicle=vehicle, user=user, points=points,
            allow_automatic=allow_automatic)
    except IntegrityError:
        return jsonify({"error": "conflict",
                        "message": "Another upload of the same points was "
                                   "in progress. Retry."}), 409
    return jsonify(result)


# ── settings ────────────────────────────────────────────────────────────────

@bp.route("/location/settings", methods=["GET"])
@api_auth_required()
def location_settings(api_user):
    """The System Parameters the app needs, plus what THIS user may do.

    The flags are hints for the screens; every endpoint still checks.
    """
    settings = VehicleLocationService().settings()
    restricted = bool(getattr(api_user, "restrict_to_assigned_vehicles",
                              False))
    can_view = _can_view_fleet(api_user)
    can_create = api_user.has_permission(CREATE)
    return jsonify({
        **settings,
        "can_view_fleet": can_view and not restricted,
        "can_capture": can_create,
        "can_capture_in_scope": can_view and can_create and not restricted,
        "assigned_vehicle_ids": AssigneeScopeService().assigned_vehicle_ids(
            api_user),
    })


# ── capture: assigned vehicle ───────────────────────────────────────────────

@bp.route("/my/vehicles/<int:vehicle_id>/locations", methods=["POST"])
@api_auth_required(CREATE)
def my_vehicle_locations(vehicle_id, api_user):
    """Manual captures and automatic tracking points for the caller's
    own vehicle. Body: {"points": [ ... up to 100 ... ]}; per point
    client_uuid, latitude, longitude, accuracy_m, captured_at (ISO-8601
    with zone), source, and for AUTOMATIC points session_uuid. Optional:
    altitude_m, speed_mps, heading_deg, is_mocked, device_id.

    200 with one result per point (created / duplicate / rejected +
    reason), so an offline queue can mark each of its rows.
    """
    vehicle = _assigned_vehicle(vehicle_id, api_user)
    if vehicle is None:
        return _not_found()
    points, error = _points_from_request()
    if error:
        return error
    return _ingest(vehicle, api_user, points, allow_automatic=True)


@bp.route("/my/vehicles/<int:vehicle_id>/tracking/start", methods=["POST"])
@api_auth_required(CREATE)
def my_vehicle_tracking_start(vehicle_id, api_user):
    vehicle = _assigned_vehicle(vehicle_id, api_user)
    if vehicle is None:
        return _not_found()
    body = request.get_json(silent=True) or {}
    try:
        session = VehicleLocationService().start_session(
            vehicle=vehicle, user=api_user,
            client_uuid=body.get("client_uuid"),
            device_id=body.get("device_id"),
            started_at=body.get("started_at"))
    except PointError as exc:
        return _bad(str(exc))
    return jsonify({"session": VehicleLocationService.session_dict(session)})


@bp.route("/my/vehicles/<int:vehicle_id>/tracking/stop", methods=["POST"])
@api_auth_required(CREATE)
def my_vehicle_tracking_stop(vehicle_id, api_user):
    vehicle = _assigned_vehicle(vehicle_id, api_user)
    if vehicle is None:
        return _not_found()
    body = request.get_json(silent=True) or {}
    try:
        session = VehicleLocationService().stop_session(
            vehicle=vehicle, user=api_user,
            client_uuid=body.get("client_uuid"),
            reason=body.get("reason"), stopped_at=body.get("stopped_at"))
    except PointError as exc:
        return _bad(str(exc))
    if session is None:
        return jsonify({"error": "not_found",
                        "message": "Tracking session not found."}), 404
    return jsonify({"session": VehicleLocationService.session_dict(session)})


# ── capture: Fleet Coordinator ──────────────────────────────────────────────

@bp.route("/vehicles/<int:vehicle_id>/locations", methods=["POST"])
@api_auth_required(CREATE)
def vehicle_locations_capture(vehicle_id, api_user):
    """A manual capture by someone standing at a vehicle that is not
    theirs -- typically a Fleet Coordinator. Needs .view as well as
    .create, and the vehicle must be inside the caller's org scope.
    Automatic points are refused here: tracking is only ever done by the
    vehicle's own assignee."""
    if not _can_view_fleet(api_user):
        return _forbidden("Recording the location of a vehicle that is not "
                          "assigned to you needs the 'vehiclelocation.view' "
                          "permission as well.")
    from app.modules.master_data.vehicle.service import VehicleService
    vehicle = VehicleService().get_visible(vehicle_id, api_user)
    if vehicle is None:
        return _not_found()
    points, error = _points_from_request()
    if error:
        return error
    return _ingest(vehicle, api_user, points, allow_automatic=False)


# ── reading ─────────────────────────────────────────────────────────────────

def _needs_a_location_permission(user):
    if not (user.has_permission(VIEW) or user.has_permission(CREATE)):
        return _forbidden("This account has no access to vehicle locations.")
    return None


@bp.route("/rescue-map/vehicles", methods=["GET"])
@api_auth_required()
def rescue_map_vehicles(api_user):
    """Markers for the Rescue Map: each reachable vehicle with its latest
    point, its status and any active tracking session, plus a count per
    status for the filter chips (counted before ?status= is applied).

    ?q=  plate / conduction / brand / model    ?status= LIVE, ...
    """
    refused = _needs_a_location_permission(api_user)
    if refused:
        return refused
    q = (request.args.get("q") or "").strip() or None
    status = (request.args.get("status") or "").strip().upper() or None
    if status and status not in STATUSES:
        return _bad(f"status must be one of {', '.join(STATUSES)}.")

    if _can_view_fleet(api_user):
        from app.modules.master_data.vehicle.service import VehicleService
        vehicles, total = VehicleService().list_page(
            user=api_user, q=q, page=1, page_size=MAP_VEHICLE_LIMIT)
    else:
        ids = AssigneeScopeService().assigned_vehicle_ids(api_user)
        query = Vehicle.query.filter(Vehicle.id.in_(ids)) if ids else None
        vehicles = query.all() if query is not None else []
        if q:
            needle = q.lower()
            vehicles = [v for v in vehicles if any(
                needle in (getattr(v, f) or "").lower() for f in (
                    "plate_number", "conduction_number", "brand", "model"))]
        total = len(vehicles)

    rows, counts = VehicleLocationService().map_rows(
        vehicles, status=status,
        include_phone=_can_view_fleet(api_user))
    return jsonify({"items": rows, "counts": counts,
                    "truncated": total > len(vehicles)})


@bp.route("/vehicles/<int:vehicle_id>/location", methods=["GET"])
@api_auth_required()
def vehicle_location_detail(vehicle_id, api_user):
    refused = _needs_a_location_permission(api_user)
    if refused:
        return refused
    vehicle = _readable_vehicle(vehicle_id, api_user)
    if vehicle is None:
        return _not_found()
    return jsonify(VehicleLocationService().detail(
        vehicle, include_phone=_can_view_fleet(api_user)))


@bp.route("/vehicles/<int:vehicle_id>/location-history", methods=["GET"])
@api_auth_required()
def vehicle_location_history(vehicle_id, api_user):
    """?source=MANUAL|AUTOMATIC  ?from= ?to= (ISO-8601 with zone)
    ?page= ?per_page= (max 200). Newest first."""
    refused = _needs_a_location_permission(api_user)
    if refused:
        return refused
    vehicle = _readable_vehicle(vehicle_id, api_user)
    if vehicle is None:
        return _not_found()
    source = (request.args.get("source") or "").strip().upper() or None
    if source and source not in SOURCES:
        return _bad("source must be MANUAL or AUTOMATIC.")
    try:
        date_from = (parse_time(request.args["from"], "from")
                     if request.args.get("from") else None)
        date_to = (parse_time(request.args["to"], "to")
                   if request.args.get("to") else None)
        page = max(1, int(request.args.get("page", 1)))
        per_page = min(200, max(1, int(request.args.get("per_page", 50))))
    except PointError as exc:
        return _bad(str(exc))
    except ValueError:
        return _bad("page and per_page must be numbers.")
    items, total = VehicleLocationService().history(
        vehicle, source=source, date_from=date_from, date_to=date_to,
        page=page, per_page=per_page)
    return jsonify({"items": items, "total": total, "page": page,
                    "per_page": per_page})
