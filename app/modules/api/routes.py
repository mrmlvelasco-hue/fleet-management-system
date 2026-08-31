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


def _is_native_client(payload):
    """Whether this caller holds its own credentials.

    Explicit opt-in from the client rather than sniffing the User-Agent
    or the Origin header: a WebView's UA is a browser UA, and guessing
    wrong in the permissive direction would hand the refresh token to a
    real browser in the response body, which is the one thing this must
    never do.
    """
    return str(payload.get("client") or "").lower() == "native"


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
    from app.modules.api.auth import (issue_refresh_token,
                                      set_refresh_cookie)
    refresh, expires_at = issue_refresh_token(user)
    body = issue_token(user)

    # A native client asks for the refresh token in the BODY.
    #
    # The cookie is the right answer for a browser -- a token
    # JavaScript can read is a token an injected script can steal -- but
    # it cannot serve a Capacitor WebView. The app's origin is
    # http://localhost while the API is on another host, so the cookie
    # is cross-site and SameSite=Lax means the browser will not send it.
    # A driver offline for a few hours would reopen the app logged out,
    # holding unsent checklist drafts: the exact failure the offline
    # work exists to prevent.
    #
    # Nothing is given up by this. The httpOnly cookie defends against
    # browser CSRF and script access, and a native app has no
    # attacker-controlled page from which to mount either. The token is
    # held in Keychain/Keystore on the device.
    if _is_native_client(payload):
        body["refresh_token"] = refresh
        body["refresh_expires_at"] = expires_at.isoformat()
        return jsonify(body)

    response = jsonify(body)
    # Browser flow unchanged: the refresh token rides in an httpOnly
    # cookie and never appears in the body, so script can neither read
    # it nor replay it cross-site.
    return set_refresh_cookie(response, refresh, expires_at)


@bp.route("/auth/refresh", methods=["POST"])
def auth_refresh():
    """Exchange the refresh cookie for a new access token.

    Deliberately takes NO Authorization header: this is the endpoint a
    freshly-opened tab calls when it has no access token at all, which
    is the situation the whole mechanism exists to serve.
    """
    from app.modules.api.auth import (REFRESH_COOKIE, issue_refresh_token,
                                      set_refresh_cookie,
                                      user_from_refresh_token)

    payload = request.get_json(silent=True) or {}
    # Body first, then cookie. A native client has no cookie jar at all,
    # which is precisely why it cannot use the browser flow.
    supplied = payload.get("refresh_token")
    user = user_from_refresh_token(
        supplied or request.cookies.get(REFRESH_COOKIE))
    if user is None:
        return jsonify({"error": "unauthorized",
                        "message": "Please sign in again."}), 401

    # Rotated on every use, so a token captured from an old response
    # stops working as soon as the legitimate client refreshes, and an
    # active session never has to re-login on a fixed schedule. Rotation
    # matters MORE on a device, not less: the token sits on hardware
    # that gets lost, lent and resold.
    refresh, expires_at = issue_refresh_token(user)
    body = issue_token(user)

    # Answered the way it was asked. A client that sent its token in the
    # body gets the replacement there; a browser gets a fresh cookie and
    # nothing readable.
    if supplied:
        body["refresh_token"] = refresh
        body["refresh_expires_at"] = expires_at.isoformat()
        return jsonify(body)

    return set_refresh_cookie(jsonify(body), refresh, expires_at)


@bp.route("/auth/logout", methods=["POST"])
def auth_logout():
    """Clear the refresh cookie.

    The access token is not revoked -- it is stateless and short-lived,
    and a revocation list would be a bigger change than it earns here.
    What this guarantees is that no NEW access token can be minted, so
    the session ends within the access token's remaining lifetime.
    """
    from app.modules.api.auth import clear_refresh_cookie
    return clear_refresh_cookie(jsonify({"ok": True}))


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
    if plate:
        # Exact-match lookup, the original behaviour. Kept separate from
        # the list path below: this answers "which vehicle is THIS
        # plate", and folding it into a fuzzy search would change what
        # existing integrations get back for a plate that is a prefix of
        # another.
        needle = plate.strip().upper()
        vehicles = [v for v in VehicleService().list(user=api_user)
                   if (v.plate_number or "").upper() == needle
                   or (v.conduction_number or "").upper() == needle]
        limit = min(request.args.get("limit", default=100, type=int), 500)
        return jsonify({"count": len(vehicles),
                       "results": [_vehicle_json(v) for v in vehicles[:limit]]})

    from app.modules.api.vehicles import build_vehicle_list
    return build_vehicle_list(api_user)


@bp.route("/vehicles/<int:vehicle_id>", methods=["GET"])
@api_auth_required("vehicle.view")
def get_vehicle(api_user, vehicle_id):
    from app.modules.master_data.vehicle.service import VehicleService
    vehicle = VehicleService().get_visible(vehicle_id, api_user)
    if vehicle is None:
        return jsonify({"error": "not_found",
                       "message": "Vehicle not found or not visible to "
                                  "this account."}), 404
    from app.modules.api.vehicles import detail_json
    return jsonify(detail_json(vehicle))


def _apply_odometer_update(vehicle, raw):
    """Shared by both the vehicle_id and plate-number odometer
    endpoints, so the two entry points can never enforce different
    rules -- there is exactly one place that decides what a valid
    reading looks like.

    Returns (response_dict, http_status). Does not commit if the
    reading is rejected.
    """
    if raw is None:
        return {"error": "bad_request",
               "message": "'odometer' is required."}, 400
    try:
        reading = int(float(str(raw).replace(",", "")))
    except (TypeError, ValueError):
        return {"error": "bad_request",
               "message": f"'odometer' must be a number, got {raw!r}."}, 400
    if reading < 0:
        return {"error": "bad_request",
               "message": "'odometer' cannot be negative."}, 400

    previous = vehicle.current_odometer
    if previous is not None and reading < previous:
        return {
            "error": "conflict",
            "message": f"Reading {reading:,} is lower than the vehicle's "
                       f"current odometer {previous:,}. An odometer cannot "
                       f"decrease; ignoring to protect PM scheduling.",
            "current_odometer": previous,
        }, 409

    vehicle.current_odometer = reading
    db.session.commit()

    return {
        "vehicle_id": vehicle.id,
        "plate_number": vehicle.plate_number or vehicle.conduction_number,
        "previous_odometer": previous,
        "current_odometer": reading,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        **{"pm_status": _vehicle_json(vehicle, include_pm=True)["pm_status"]},
    }, 200


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
    body, status = _apply_odometer_update(vehicle, payload.get("odometer"))
    return jsonify(body), status


@bp.route("/vehicles/by-plate/odometer", methods=["POST"])
@api_auth_required("vehicle.update")
def update_odometer_by_plate(api_user):
    """Same as POST /vehicles/<id>/odometer, but looked up by plate or
    conduction number instead of the internal vehicle id -- for a
    source system (an SMS-consolidation tool, a fleet-card reader) that
    only knows the plate, not this system's own database id.

    A body parameter rather than a URL path segment specifically
    because a plate number can contain characters ('/', spaces) that
    would need careful URL-encoding in a path; a JSON body avoids that
    entirely for the calling system.
    """
    payload = request.get_json(silent=True) or {}
    plate = (payload.get("plate_number") or "").strip()
    if not plate:
        return jsonify({"error": "bad_request",
                       "message": "'plate_number' is required."}), 400

    from app.modules.master_data.vehicle.service import VehicleService
    needle = plate.upper()
    match = next((v for v in VehicleService().list(user=api_user)
                 if (v.plate_number or "").upper() == needle
                 or (v.conduction_number or "").upper() == needle), None)
    if match is None:
        return jsonify({
            "error": "not_found",
            "message": f"No vehicle found with plate or conduction number "
                      f"'{plate}'.",
        }), 404

    body, status = _apply_odometer_update(match, payload.get("odometer"))
    return jsonify(body), status


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


# list_maintenance_orders moved to api/maintenance_orders.py
