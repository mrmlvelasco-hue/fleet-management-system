"""Vehicle list endpoint for the React Master Data screens.

This is the FIRST list endpoint, and the envelope it returns
(`items` / `total` / `page` / `page_size` / `pages`) is deliberately
generic: every other Master Data list -- Drivers, Tires, Batteries,
Vendors, Branches -- will return the same shape so one frontend
scaffold can drive all of them. Changing the envelope later means
changing every consumer, so it is worth being boring here.

A note on where paging happens, because it is not obvious and it
matters:

`VehicleService.list()` applies org scoping in PYTHON, after `.all()` --
a vehicle is visible if the user created it OR their org scope covers
its branch, and the "created_by" half cannot be expressed in the same
SQL filter as the scope half. So the rows must be materialised and
filtered before they can be counted or sliced.

Paging in SQL would therefore be wrong, not merely different: LIMIT
would slice rows the user may not see, scoped-out rows would silently
consume slots on the page, and `total` would disagree with what the
list actually contains.

The cost is that the full visible set is loaded per request. For a
504-vehicle fleet that is fine. It will NOT be fine for high-volume
transaction modules (Trip Tickets, Fuel), and those will need org
scoping pushed down into SQL before they get a list endpoint. Flagged
here rather than discovered later.
"""
from flask import jsonify, request

# Sort keys the client may ask for, mapped to the attribute used. An
# allow-list rather than passing the parameter through: an unknown key
# is rejected so a column header can never claim to be sorting while
# quietly doing nothing.
_SORT_KEYS = {
    "plate": lambda v: (v.plate_number or v.conduction_number or "").upper(),
    "vehicle": lambda v: f"{v.brand} {v.model}".upper(),
    "type": lambda v: (v.vehicle_type.name if v.vehicle_type else "").upper(),
    "status": lambda v: (v.status or ""),
    "odometer": lambda v: v.current_odometer or 0,
    "year": lambda v: v.year or 0,
    "branch": lambda v: (v.branch.name if v.branch else "").upper(),
}

_VALID_STATUSES = {"ACTIVE", "INACTIVE", "IN_REPAIR", "DISPOSED"}


def _bad(message):
    return jsonify({"error": "bad_request", "message": message}), 400


def build_vehicle_list(api_user):
    """Filter/sort/page the caller's visible vehicles.

    Called by the existing GET /api/v1/vehicles route rather than
    registering a second one: two endpoints over the same data drift,
    and the older one already has external consumers.
    """
    q = (request.args.get("q") or "").strip().lower()
    status = (request.args.get("status") or "").strip().upper()
    sort = (request.args.get("sort") or "plate").strip().lower()
    direction = (request.args.get("dir") or "asc").strip().lower()

    if status and status not in _VALID_STATUSES:
        # Silently ignoring a typo would show the full list under a
        # filter the user believes is applied -- every row real, nothing
        # visibly wrong.
        return _bad(f"Unknown status '{status}'.")
    if sort not in _SORT_KEYS:
        return _bad(f"Unknown sort key '{sort}'.")
    if direction not in ("asc", "desc"):
        return _bad("dir must be 'asc' or 'desc'.")

    try:
        page = int(request.args.get("page", 1))
        page_size = int(request.args.get("page_size", 20))
    except (TypeError, ValueError):
        return _bad("page and page_size must be integers.")
    if page < 1 or page_size < 1:
        return _bad("page and page_size must be 1 or greater.")
    page_size = min(page_size, 200)

    branch_id = request.args.get("branch_id")
    vehicle_type_id = request.args.get("vehicle_type_id")
    try:
        branch_id = int(branch_id) if branch_id else None
        vehicle_type_id = int(vehicle_type_id) if vehicle_type_id else None
    except (TypeError, ValueError):
        return _bad("branch_id and vehicle_type_id must be integers.")

    from app.modules.master_data.vehicle.service import VehicleService

    # DISPOSED units are excluded from the everyday list by the service,
    # but must be reachable when explicitly filtered for -- otherwise
    # asking for disposed vehicles returns nothing, which reads as "there
    # are none" rather than "the list will not show you those".
    include_disposed = status == "DISPOSED"

    rows = VehicleService().list(user=api_user, branch_id=branch_id,
                                 include_disposed=include_disposed)

    if vehicle_type_id is not None:
        rows = [v for v in rows if v.vehicle_type_id == vehicle_type_id]
    if status:
        rows = [v for v in rows if v.status == status]
    if q:
        # Searches every identifier a person might have in hand: a plate
        # off the vehicle, a conduction number for one still awaiting
        # plates, or just the make.
        def matches(v):
            haystack = " ".join(filter(None, [
                v.plate_number, v.conduction_number, v.brand, v.model,
                v.vehicle_type.name if v.vehicle_type else None,
                v.branch.name if v.branch else None,
            ])).lower()
            return q in haystack

        rows = [v for v in rows if matches(v)]

    rows.sort(key=_SORT_KEYS[sort], reverse=(direction == "desc"))

    total = len(rows)
    pages = max(1, (total + page_size - 1) // page_size)
    start = (page - 1) * page_size
    window = rows[start:start + page_size]

    return jsonify({
        "items": [_serialise(v) for v in window],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": pages,
        # Kept for the original consumers of this endpoint, which predate
        # paging. Same rows, same order -- only the key names differ, so
        # nothing that reads `results`/`count` has to change.
        "results": [_serialise(v) for v in window],
        "count": total,
    })


def _serialise(v):
    return {
        "id": v.id,
        "plate_number": v.plate_number,
        "conduction_number": v.conduction_number,
        "brand": v.brand,
        "model": v.model,
        "year": v.year,
        "vehicle_type": v.vehicle_type.name if v.vehicle_type else None,
        "vehicle_type_id": v.vehicle_type_id,
        "status": v.status,
        "current_odometer": v.current_odometer,
        "branch": v.branch.name if v.branch else None,
        "branch_id": v.branch_id,
    }
