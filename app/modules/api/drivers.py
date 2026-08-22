"""Driver / Assignee list endpoint for the React Master Data screens.

Built from `docs/parity-driver.md` (React repo), which was written from
`driver_list.html`. Not from a component, and not from the Driver model:
the model has 35 columns and the list shows 8 of them.

The module is **Driver / Assignee Master**, not "Drivers". It holds
drivers, consultants and third-party delivery personnel, and
`assignee_type` decides which. Calling it Drivers anywhere user-facing
misdescribes what is in it.

Envelope matches the vehicle list exactly, so one frontend scaffold
drives both. Paging happens in PYTHON for the same reason it does there:
`DriverService.list()` applies org scoping after `.all()` -- a driver is
visible if the caller created the record OR their org scope covers its
branch, and those two halves cannot be expressed in one SQL filter. So
rows must be materialised before they can be counted or sliced. LIMIT
would slice rows the user may not see, scoped-out rows would silently
consume slots on the page, and `total` would disagree with the list.

The fleet's driver roster is a few hundred rows. If it ever approaches
the vehicle list's scale, scoping needs pushing into SQL first --
`VehicleService.list_page` is the worked example.
"""
from datetime import date

from flask import jsonify, request

from app.modules.api.auth import api_auth_required
from app.modules.api.routes import bp

#: Three values, and NOT the vehicle vocabulary. SUSPENDED has no
#: vehicle equivalent and matters on its own: a suspended driver must
#: remain findable but must never read as assignable.
_DRIVER_STATUSES = ("ACTIVE", "INACTIVE", "SUSPENDED")

#: Days ahead that counts as "expiring", matching
#: DriverService.get_expiring_licenses' own default.
_EXPIRING_DAYS = 30


def _bad(message):
    return jsonify({"error": "bad_request", "message": message}), 400


def _display_name(d):
    """"Last, First M." -- the Jinja list's format.

    The middle name renders as an INITIAL with a full stop, never in
    full. Assembled server-side so the two apps cannot show a person
    under two different names, which is the kind of difference that
    makes someone think they are looking at two records.
    """
    name = f"{d.last_name}, {d.first_name}"
    if d.middle_name:
        name = f"{name} {d.middle_name[0]}."
    return name


def _serialise(d):
    return {
        "id": d.id,
        "person_id": d.person_id,
        "employee_number": d.employee_number,
        "display_name": _display_name(d),
        "first_name": d.first_name,
        "last_name": d.last_name,
        "assignee_type": d.assignee_type,
        "license_number": d.license_number,
        "license_expiry": d.license_expiry.isoformat()
        if d.license_expiry else None,
        "license_type": d.license_type,
        # Computed HERE, not in the browser. "Expired" is a comparison
        # against today, and doing it client-side makes a compliance
        # flag depend on the user's device clock.
        #
        # A driver with no licence recorded is NOT expired. Consultants
        # have none at all, and flagging them would put a red badge on
        # every non-driver in the list and train people to ignore the
        # one that matters.
        "license_expired": bool(
            d.license_expiry and d.license_expiry < date.today()),
        "branch": d.branch.name if d.branch else None,
        "branch_id": d.branch_id,
        "department": d.department.name if d.department else None,
        "status": d.status,
    }


@bp.route("/drivers", methods=["GET"])
@api_auth_required("driver.view")
def list_drivers(api_user):
    """Filter/sort/page the caller's visible drivers."""
    from app.modules.master_data.driver.service import DriverService

    q = (request.args.get("q") or "").strip().lower()
    status = (request.args.get("status") or "").strip().upper()
    assignee_type = (request.args.get("assignee_type") or "").strip().upper()
    expiring = request.args.get("license_expiring") == "1"

    if status and status not in _DRIVER_STATUSES:
        return _bad(f"Unknown status '{status}'.")

    try:
        branch_id = request.args.get("branch_id")
        branch_id = int(branch_id) if branch_id else None
        page = int(request.args.get("page", 1))
        page_size = int(request.args.get("page_size", 20))
    except (TypeError, ValueError):
        return _bad("branch_id, page and page_size must be integers.")
    if page < 1 or page_size < 1:
        return _bad("page and page_size must be 1 or greater.")
    page_size = min(page_size, 200)

    # include_inactive=True: the list has its own status filter, and
    # hiding INACTIVE rows here would make that filter unable to show
    # them. Soft-deleted rows are a separate concern from status.
    rows = DriverService().list(include_inactive=True, branch_id=branch_id,
                                user=api_user)

    if status:
        rows = [d for d in rows if d.status == status]
    if assignee_type:
        rows = [d for d in rows if d.assignee_type == assignee_type]
    if q:
        rows = [d for d in rows
                if q in (d.employee_number or "").lower()
                or q in (d.first_name or "").lower()
                or q in (d.last_name or "").lower()
                or q in (d.license_number or "").lower()]
    if expiring:
        # Expired OR expiring within the window. A licence that lapsed
        # last week is more urgent than one lapsing next week, so a
        # filter that showed only the future would hide the worse case.
        from datetime import timedelta
        threshold = date.today() + timedelta(days=_EXPIRING_DAYS)
        rows = [d for d in rows if d.license_expiry
                and d.license_expiry <= threshold]

    total = len(rows)
    start = (page - 1) * page_size
    window = rows[start:start + page_size]

    return jsonify({
        "items": [_serialise(d) for d in window],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, (total + page_size - 1) // page_size),
    })
