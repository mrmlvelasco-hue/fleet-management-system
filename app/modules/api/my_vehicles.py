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

    v.current_odometer = reading
    db.session.commit()
    return jsonify(_row(v))
