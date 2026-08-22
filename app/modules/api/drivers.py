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
from app.modules.api.coercion import Coercer, FieldValueError
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
        # Not the id -- the list does not render photos. But with
        # REQUIRE_ASSIGNEE_PHOTO waived for migration, "which records
        # still need a photograph" becomes a real question, and the
        # list is where it gets answered.
        "has_photo": d.photo_attachment_id is not None,
    }


def _filtered(api_user):
    """The caller's visible drivers, narrowed by the query string.

    Shared by the list and its export. Two filter implementations over
    one query string is how a spreadsheet ends up meaning something
    subtly different from the screen it came from -- and the
    spreadsheet is the copy that gets emailed on.

    Returns (rows, None) or (None, error_response).
    """
    from app.modules.master_data.driver.service import DriverService

    q = (request.args.get("q") or "").strip().lower()
    status = (request.args.get("status") or "").strip().upper()
    assignee_type = (request.args.get("assignee_type") or "").strip().upper()
    expiring = request.args.get("license_expiring") == "1"

    if status and status not in _DRIVER_STATUSES:
        return None, _bad(f"Unknown status '{status}'.")

    try:
        branch_id = request.args.get("branch_id")
        branch_id = int(branch_id) if branch_id else None
    except (TypeError, ValueError):
        return None, _bad("branch_id must be an integer.")

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

    return rows, None


@bp.route("/drivers", methods=["GET"])
@api_auth_required("driver.view")
def list_drivers(api_user):
    """Filter/sort/page the caller's visible drivers."""
    rows, error = _filtered(api_user)
    if error:
        return error

    try:
        page = int(request.args.get("page", 1))
        page_size = int(request.args.get("page_size", 20))
    except (TypeError, ValueError):
        return _bad("page and page_size must be integers.")
    if page < 1 or page_size < 1:
        return _bad("page and page_size must be 1 or greater.")
    page_size = min(page_size, 200)

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


@bp.route("/drivers/summary", methods=["GET"])
@api_auth_required("driver.view")
def drivers_summary(api_user):
    """Counts for the stat chips.

    Expiring and expired are kept APART. They are two different facts
    needing two different responses -- renew soon, versus stop
    dispatching this person now -- and one combined figure would bury
    the urgent case inside the routine one.

    Drivers with no licence recorded (consultants, third-party delivery)
    count toward neither. Including them would make the chip permanently
    non-zero and therefore ignored.
    """
    from datetime import timedelta

    from app.modules.master_data.driver.service import DriverService

    rows = DriverService().list(include_inactive=True, user=api_user)
    today = date.today()
    threshold = today + timedelta(days=_EXPIRING_DAYS)

    licensed = [d for d in rows if d.license_expiry]
    return jsonify({
        "total": len(rows),
        "active": sum(1 for d in rows if d.status == "ACTIVE"),
        "suspended": sum(1 for d in rows if d.status == "SUSPENDED"),
        "license_expired": sum(1 for d in licensed
                               if d.license_expiry < today),
        "license_expiring": sum(1 for d in licensed
                                if today <= d.license_expiry <= threshold),
    })


@bp.route("/drivers/export.xlsx", methods=["GET"])
@api_auth_required("driver.view")
def drivers_export(api_user):
    """The roster as an Excel file, respecting the active filter.

    Same divergence from Flask as the vehicle export, for the same
    reason: Flask's Export dumps the whole table regardless of what is
    on screen, and handing over the entire roster when the user was
    looking at one branch is what gets noticed in front of a client.
    Clearing the filters still gets everyone.

    No paging. The list caps page_size at 200; inheriting that would
    produce a truncated file that looks complete.
    """
    from io import BytesIO

    from flask import send_file

    from app.core.reporting.generators import generate_driver_list_xlsx

    rows, error = _filtered(api_user)
    if error:
        return error

    filename, xlsx = generate_driver_list_xlsx(rows)
    return send_file(
        BytesIO(xlsx), as_attachment=True, download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument"
                 ".spreadsheetml.sheet")


def _emergency_contacts(driver):
    """Related Emergency Contact records, keyed by person_record_id.

    Separate from the driver's own emergency_contact_person /
    _number columns, which are ALSO shown under the same heading in
    Jinja. Two records of the same fact that can disagree, with nothing
    reconciling them -- mirrored here rather than quietly merged, and
    flagged in docs/parity-driver.md for the client.
    """
    from app.modules.master_data.driver.service import EmergencyContactService
    return [
        {"id": c.id,
         "contact_name": c.contact_name,
         "relationship_type": c.relationship_type,
         "contact_number": c.contact_number}
        for c in EmergencyContactService().list_for_person(driver.person_id)]


def _assigned_vehicles(driver):
    """Vehicles this person is currently assigned.

    The only place in the system that answers "what is this person
    driving", and what makes the expired-licence flag actionable: an
    expired licence matters precisely because a vehicle is attached to
    it. The id is carried so the client can link back to vehicle
    detail, which is the value of the section.
    """
    from app.modules.master_data.vehicle.models import Vehicle
    rows = (Vehicle.query
            .filter_by(assigned_driver_id=driver.id, is_active=True)
            .order_by(Vehicle.plate_number)
            .all())
    return [
        {"id": v.id,
         "plate_number": v.plate_number or v.conduction_number,
         "brand": v.brand,
         "model": v.model,
         "branch": v.branch.name if v.branch else None}
        for v in rows]


@bp.route("/drivers/<int:driver_id>", methods=["GET"])
@api_auth_required("driver.view")
def driver_detail(api_user, driver_id):
    """One driver, shaped for the detail screen.

    Built from docs/parity-driver.md §7, itself from
    driver_detail.html. Not from the model: that has 35 columns, and the
    screen shows a specific subset under specific headings.

    `show_license` and `show_business` are decided HERE because
    driver_detail.html decides them in Jinja -- unlike the FORM, which
    decides the same thing in jQuery. The detail screen is the more
    honest implementation of the rule, so the API follows it. Sending
    the decision rather than the raw assignee_type means the client
    cannot re-derive the rule slightly differently and drift.
    """
    from app.modules.master_data.driver.service import DriverService

    d = DriverService().get_visible(driver_id, api_user)
    if d is None:
        # Same 404 a missing record gives, so this cannot be used to
        # discover which ids exist.
        return jsonify({"error": "not_found",
                        "message": "Driver not found or not visible to "
                                   "this account."}), 404

    return jsonify(_serialise_detail(d))


def _serialise_detail(d):
    """One shape for GET, POST and PUT.

    A write that answered in a different shape from the read would make
    the client re-fetch after every save, or worse, render a
    half-populated record from the response it just got.
    """
    return {
        # Basic
        "id": d.id,
        "person_id": d.person_id,
        "employee_number": d.employee_number,
        # The MODEL's own property ("First M. Last Suffix"). The list
        # uses "Last, First M." -- both correct for their screen. What
        # must not happen is a third format invented in the API.
        "full_name": d.full_name,
        "first_name": d.first_name,
        "middle_name": d.middle_name,
        "last_name": d.last_name,
        "suffix": d.suffix,
        "nickname": d.nickname,
        # The ID, not a URL: the client builds the download URL from the
        # attachment endpoint it already uses, so there is one
        # definition of where an attachment lives. Null when absent --
        # 0 would be a valid-looking id that 404s on fetch.
        "photo_attachment_id": d.photo_attachment_id,
        "assignee_type": d.assignee_type,
        "status": d.status,
        # Organization
        "branch": d.branch.name if d.branch else None,
        "branch_id": d.branch_id,
        "department": d.department.name if d.department else None,
        "department_id": d.department_id,
        "section": d.section,
        "position": d.position,
        "job_title": d.job_title,
        "cost_center": d.cost_center,
        "employment_status": d.employment_status,
        "employment_type": d.employment_type,
        # License
        "license_number": d.license_number,
        "license_type": d.license_type,
        "license_expiry": d.license_expiry.isoformat()
        if d.license_expiry else None,
        "license_expired": bool(
            d.license_expiry and d.license_expiry < date.today()),
        # Contact. `phone` is labelled "Mobile Number" on screen; the
        # roster distinguishes mobile from office and home.
        "phone": d.phone,
        "office_number": d.office_number,
        "home_number": d.home_number,
        "email": d.email,
        "complete_address": d.complete_address,
        # Business
        "business_name": d.business_name,
        "business_contact_no": d.business_contact_no,
        "business_address": d.business_address,
        # Emergency
        "emergency_contact_person": d.emergency_contact_person,
        "emergency_contact_number": d.emergency_contact_number,
        "emergency_contacts": _emergency_contacts(d),
        # Assignment
        "assigned_vehicles": _assigned_vehicles(d),
        # Section visibility, decided server-side (see docstring).
        "show_license": d.assignee_type == "DRIVER",
        "show_business": d.assignee_type in ("THIRD_PARTY_DELIVERY",
                                             "CONSULTANT"),
    }


# ── Write ───────────────────────────────────────────────────────────────────

#: Fields a client may set. An allow-list, not **payload: `person_id` is
#: GENERATED by the service, and letting a client supply one would break
#: the traceability the generator exists to provide. `id`,
#: `photo_attachment_id` and the audit columns are excluded for the same
#: reason -- they are the system's record of what happened, not input.
_WRITABLE = [
    # Basic
    "employee_number", "first_name", "middle_name", "last_name", "suffix",
    "nickname", "assignee_type", "status",
    # License
    "license_number", "license_expiry", "license_type",
    # Organization
    "branch_id", "department_id", "section", "cost_center", "position",
    "job_title", "employment_status", "employment_type",
    # Contact
    "phone", "office_number", "home_number", "email", "complete_address",
    "emergency_contact_person", "emergency_contact_number",
    # Business
    "business_name", "business_address", "business_contact_no",
]

#: Labels match the Jinja form's, so both apps report the same problem in
#: the same words.
_COERCER = Coercer(
    ints={"branch_id": "Branch", "department_id": "Department"},
    dates={"license_expiry": "License Expiry"},
)

#: Service errors carry no field name, so they would otherwise all land
#: at form level -- "something is wrong" against a six-section form.
_FIELD_FOR_MESSAGE = [
    ("license", "license_number"),
    ("photo", "photo"),
    ("employee number", "employee_number"),
    ("person id", "person_id"),
]


def _field_for(exc):
    """Best-effort attribution of a service error to an input."""
    if isinstance(exc, FieldValueError):
        return exc.field
    text = str(exc).lower()
    for needle, field in _FIELD_FOR_MESSAGE:
        if needle in text:
            return field
    # "_" is the honest answer when the error cannot be attributed:
    # blaming a field that may be correct is worse than form level.
    return "_"


def _read_payload():
    """Fields and an optional photo, from JSON or multipart.

    Returns (payload_dict, photo_file_or_None).

    Multipart values are all strings, which is what the coercer is for;
    JSON may already carry typed values, which it passes through
    untouched.
    """
    if request.files or request.form:
        return dict(request.form), request.files.get("photo")
    return (request.get_json(silent=True) or {}), None


def _write_errors():
    from app.modules.master_data.driver import service as dsvc
    return (FieldValueError, dsvc.DuplicateDriverError,
            dsvc.InvalidAssigneeError)


def _validation_response(exc):
    field = _field_for(exc)
    return jsonify({"error": "validation", "message": str(exc),
                    "fields": {field: str(exc)}}), 400


@bp.route("/drivers", methods=["POST"])
@api_auth_required("driver.create")
def create_driver(api_user):
    """Enrol a driver or assignee.

    Wraps DriverService.create, which owns every rule -- including the
    two configurable creation constraints (REQUIRE_DRIVER_LICENSE,
    REQUIRE_ASSIGNEE_PHOTO). Re-checking any of them here would be a
    second definition that drifts from the Jinja form.

    Accepts EITHER JSON or multipart/form-data.

    Both are needed. REQUIRE_ASSIGNEE_PHOTO defaults to strict, and a
    photo can only arrive as a file -- so a JSON-only endpoint could
    never create a driver on a normally-configured install, and would
    work solely during migration with the parameter switched off. That
    is a create endpoint that breaks the moment the client finishes
    migrating, which is exactly when they would stop expecting
    breakage.

    JSON stays supported because the migration importer has no photo to
    send and building a multipart body for a field it does not have
    would be noise.
    """
    from app.modules.master_data.driver.service import DriverService

    payload, photo_file = _read_payload()
    try:
        fields = _COERCER.fields(payload, _WRITABLE)
        driver = DriverService().create(user=api_user, photo_file=photo_file,
                                        **fields)
    except _write_errors() as exc:
        return _validation_response(exc)
    except TypeError as exc:
        # A required argument the service names positionally.
        return _bad(str(exc))

    return jsonify(_serialise_detail(driver)), 201


@bp.route("/drivers/<int:driver_id>", methods=["PUT", "PATCH"])
@api_auth_required("driver.update")
def update_driver(api_user, driver_id):
    """Partial update.

    Only the keys PRESENT in the payload are applied. A sparse request
    must not blank the fields it omits -- the form posts whole sections,
    and anything else sending a subset would otherwise silently clear
    the rest of the record.
    """
    from app.modules.master_data.driver.service import DriverService

    svc = DriverService()
    # Visibility first, and the same 404 a missing record gives, so a
    # write attempt cannot be used to discover which ids exist.
    if svc.get_visible(driver_id, api_user) is None:
        return jsonify({"error": "not_found",
                        "message": "Driver not found or not visible to "
                                   "this account."}), 404

    payload, photo_file = _read_payload()
    try:
        fields = _COERCER.fields(payload, _WRITABLE)
        driver = svc.update(driver_id, user=api_user, photo_file=photo_file,
                            **fields)
    except _write_errors() as exc:
        return _validation_response(exc)

    return jsonify(_serialise_detail(driver))
