"""REST API v1.

Scoped deliberately to the two confirmed consumers rather than exposing
everything:

  * GPS / telematics units posting odometer readings. This is the
    highest-value endpoint in the system -- Vehicle.current_odometer is
    what the PM due-calculation engine reads, so an automatic feed turns
    PM scheduling from "someone remembers to type the km in" into
    something that maintains itself.
  * A mobile app reading fleet and PM data.

Everything is versioned under /api/v1/ so a future breaking change can
ship as /api/v2/ without stranding devices already in the field --
firmware on a vehicle-mounted unit is not something you can redeploy
quickly.
"""
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

from app.extensions import db
from app.modules.api.auth import api_auth_required, issue_token
from app.core.security.password import verify_password

bp = Blueprint("api_v1", __name__, url_prefix="/api/v1")


def _vehicle_json(v, include_pm=False):
    data = {
        "id": v.id,
        "plate_number": v.plate_number,
        "conduction_number": v.conduction_number,
        "brand": v.brand,
        "model": v.model,
        "year": v.year,
        "status": v.status,
        "current_odometer": v.current_odometer,
        "branch": v.branch.name if v.branch else None,
        "assigned_driver": (v.assigned_driver.full_name
                           if v.assigned_driver else None),
    }
    if include_pm:
        from app.core.maintenance.pm_package_recommendation_service import (
            PMPackageRecommendationService)
        rec = PMPackageRecommendationService().recommend(v)
        pkg = rec.get("recommended_package")
        data["pm_status"] = {
            "status": rec["status"],
            "due_by": rec["due_by"],
            "due_odometer": rec["due_odometer"],
            "due_date": (rec["due_date"].isoformat()
                        if rec["due_date"] else None),
            "reason": rec["reason"],
            "recommended_package_id": pkg.id if pkg else None,
            "beyond_defined_cycle": rec.get("beyond_defined_cycle", False),
        }
    return data


@bp.route("/auth/token", methods=["POST"])
def auth_token():
    """Exchange username/password for a bearer token.

    Intentionally NOT rate-limited here -- that belongs at the reverse
    proxy / WAF layer where it can be applied per-IP across the whole
    app, rather than being re-implemented per endpoint.
    """
    payload = request.get_json(silent=True) or {}
    username = (payload.get("username") or "").strip()
    password = payload.get("password") or ""
    if not username or not password:
        return jsonify({"error": "bad_request",
                       "message": "username and password are required."}), 400

    from app.modules.user_management.models import User
    user = User.query.filter_by(username=username).first()
    # Same generic message whether the username is unknown or the
    # password is wrong, so the endpoint can't be used to discover which
    # usernames exist.
    if user is None or not user.is_active or not verify_password(
            user.password_hash, password):
        return jsonify({"error": "unauthorized",
                       "message": "Invalid username or password."}), 401
    return jsonify(issue_token(user))


@bp.route("/me", methods=["GET"])
@api_auth_required()
def me(api_user):
    """Who am I, and what may I do -- lets a mobile app hide actions the
    signed-in user can't perform, using the real permission list."""
    return jsonify({
        "id": api_user.id,
        "username": api_user.username,
        "full_name": api_user.full_name,
        "email": api_user.email,
        "roles": [r.name for r in api_user.roles],
        "permissions": sorted(
            p.code for r in api_user.roles for p in r.permissions),
    })


@bp.route("/vehicles", methods=["GET"])
@api_auth_required("vehicle.view")
def list_vehicles(api_user):
    from app.modules.master_data.vehicle.service import VehicleService
    plate = request.args.get("plate")
    vehicles = VehicleService().list(user=api_user)
    if plate:
        needle = plate.strip().upper()
        vehicles = [v for v in vehicles
                   if (v.plate_number or "").upper() == needle
                   or (v.conduction_number or "").upper() == needle]
    limit = min(request.args.get("limit", default=100, type=int), 500)
    return jsonify({"count": len(vehicles),
                   "results": [_vehicle_json(v) for v in vehicles[:limit]]})


@bp.route("/vehicles/<int:vehicle_id>", methods=["GET"])
@api_auth_required("vehicle.view")
def get_vehicle(api_user, vehicle_id):
    from app.modules.master_data.vehicle.service import VehicleService
    vehicle = VehicleService().get_visible(vehicle_id, api_user)
    if vehicle is None:
        return jsonify({"error": "not_found",
                       "message": "Vehicle not found or not visible to "
                                  "this account."}), 404
    return jsonify(_vehicle_json(vehicle, include_pm=True))


@bp.route("/vehicles/<int:vehicle_id>/odometer", methods=["POST"])
@api_auth_required("vehicle.update")
def update_odometer(api_user, vehicle_id):
    """Record an odometer reading -- the GPS/telematics entry point.

    Two rules matter here and are enforced server-side, because the
    caller is an unattended device:

    * An odometer cannot go BACKWARDS. A GPS glitch, a device swap, or a
      replayed message would otherwise rewind the reading and silently
      reset every PM due calculation for that vehicle. A lower reading is
      rejected with 409 rather than quietly ignored, so the sending
      system can log and surface it.
    * The response includes the resulting PM status, so the telematics
      platform learns immediately that this reading has just made a
      service due -- without having to poll a second endpoint.
    """
    from app.modules.master_data.vehicle.service import VehicleService
    vehicle = VehicleService().get_visible(vehicle_id, api_user)
    if vehicle is None:
        return jsonify({"error": "not_found",
                       "message": "Vehicle not found or not visible to "
                                  "this account."}), 404

    payload = request.get_json(silent=True) or {}
    raw = payload.get("odometer")
    if raw is None:
        return jsonify({"error": "bad_request",
                       "message": "'odometer' is required."}), 400
    try:
        reading = int(float(str(raw).replace(",", "")))
    except (TypeError, ValueError):
        return jsonify({"error": "bad_request",
                       "message": f"'odometer' must be a number, got "
                                  f"{raw!r}."}), 400
    if reading < 0:
        return jsonify({"error": "bad_request",
                       "message": "'odometer' cannot be negative."}), 400

    previous = vehicle.current_odometer
    if previous is not None and reading < previous:
        return jsonify({
            "error": "conflict",
            "message": f"Reading {reading:,} is lower than the vehicle's "
                       f"current odometer {previous:,}. An odometer cannot "
                       f"decrease; ignoring to protect PM scheduling.",
            "current_odometer": previous,
        }), 409

    vehicle.current_odometer = reading
    db.session.commit()

    return jsonify({
        "vehicle_id": vehicle.id,
        "plate_number": vehicle.plate_number or vehicle.conduction_number,
        "previous_odometer": previous,
        "current_odometer": reading,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        **{"pm_status": _vehicle_json(vehicle, include_pm=True)["pm_status"]},
    })


@bp.route("/vehicles/<int:vehicle_id>/pm-status", methods=["GET"])
@api_auth_required("vehicle.view")
def vehicle_pm_status(api_user, vehicle_id):
    """PM due status on its own -- for a dashboard or a device that
    wants to check without posting a reading."""
    from app.modules.master_data.vehicle.service import VehicleService
    vehicle = VehicleService().get_visible(vehicle_id, api_user)
    if vehicle is None:
        return jsonify({"error": "not_found",
                       "message": "Vehicle not found or not visible to "
                                  "this account."}), 404
    return jsonify(_vehicle_json(vehicle, include_pm=True)["pm_status"])


@bp.route("/maintenance-orders", methods=["GET"])
@api_auth_required("maintenanceorder.view")
def list_maintenance_orders(api_user):
    from app.modules.transactions.maintenance_order.service import (
        MaintenanceOrderService)
    status = request.args.get("status")
    orders = MaintenanceOrderService().list(user=api_user)
    if status:
        orders = [o for o in orders if o.status == status.upper()]
    limit = min(request.args.get("limit", default=100, type=int), 500)
    return jsonify({
        "count": len(orders),
        "results": [{
            "id": o.id,
            "document_number": o.document_number,
            "status": o.status,
            "vehicle_id": o.vehicle_id,
            "plate_number": (o.vehicle.plate_number
                            or o.vehicle.conduction_number) if o.vehicle else None,
            "maintenance_type": (o.maintenance_type.name
                                if o.maintenance_type else None),
            "scheduled_date": (o.scheduled_date.isoformat()
                              if o.scheduled_date else None),
            "completed_date": (o.completed_date.isoformat()
                              if o.completed_date else None),
            "odometer_at_service": o.odometer_at_service,
        } for o in orders[:limit]],
    })
