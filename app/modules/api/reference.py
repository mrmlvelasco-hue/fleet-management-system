"""Reference data for JWT clients — the Vehicle form's dropdowns.

In Jinja these lists come from `api_search`, which is gated by
@login_required: SESSION auth. A bearer-token client cannot reach any of
it, which is why the React form shipped Brand, Model, Transmission and
Fuel Type as free-text inputs.

That is not merely cosmetic. Brand and model are still caught on save
(the write endpoints pass strict=True), but `transmission`, `fuel_type`,
`component_group` and `vehicle_body_type` have **no** server-side
membership check anywhere — they are plain string columns whose only
constraint has always been that the Jinja dropdown could not produce
anything else. Free text there writes values Flask never could, and they
resurface in the Vehicle Register report's own Fuel and Transmission
columns.

Rather than add validation here (a second definition of a rule that
lives in Lookup Maintenance), this restores the constraint where Flask
puts it: the client is given the same closed list, from the same
service.

Everything below wraps an existing service. Nothing re-implements a
rule, and nothing re-queries a model directly except where the service
layer has no method for it.

**Permission.** All of these are gated on `vehicle.view`, not on the
owning module's permission, for the reason already recorded on
/vehicle-types: reference data needed to FILL a form must not require
`vehiclebrand.view` or `lookup.view`, or the dropdown renders empty for
exactly the people expected to enrol vehicles — a required field with no
options is a form that cannot be submitted. This matches api_search's
own documented reasoning.
"""
from flask import jsonify, request

from app.modules.api.auth import api_auth_required
from app.modules.api.routes import bp


def _bad(message):
    return jsonify({"error": "bad_request", "message": message}), 400


# ── Brands and models ───────────────────────────────────────────────────────

@bp.route("/reference/vehicle-brands", methods=["GET"])
@api_auth_required("vehicle.view")
def reference_vehicle_brands(api_user):
    """The Brand master list, in the order the Jinja form renders it."""
    from app.modules.master_data.vehicle_brand.service import (
        VehicleBrandService)
    return jsonify({"items": [
        {"id": b.id, "name": b.name}
        for b in VehicleBrandService().list()]})


@bp.route("/reference/vehicle-models", methods=["GET"])
@api_auth_required("vehicle.view")
def reference_vehicle_models(api_user):
    """Models for one brand — the cascade behind Brand → Model.

    No brand_id yields an empty list rather than every model, mirroring
    api_search. Returning the full list would offer a Toyota Vios under
    Isuzu, and strict=True would then reject the save with an error the
    user could not have predicted from the dropdown they were given.

    `brand_id` is echoed back so the client can tell "no brand chosen"
    from "this brand has no models yet". Flask renders those as two
    different messages; collapsing them would leave someone unable to
    distinguish a missing selection from missing master data.
    """
    from app.modules.master_data.vehicle_brand.service import (
        VehicleModelService)

    raw = request.args.get("brand_id")
    if not raw:
        return jsonify({"items": [], "brand_id": None})
    try:
        brand_id = int(raw)
    except (TypeError, ValueError):
        return _bad("brand_id must be an integer.")

    models = VehicleModelService().list(brand_id=brand_id)
    return jsonify({
        "items": [{"id": m.id, "name": m.name} for m in models],
        "brand_id": brand_id,
    })


# ── Lookups ─────────────────────────────────────────────────────────────────

def _known_lookup_types():
    """Types the system recognises: whatever is registered in code, plus
    anything already in the DB. Both matter — the registry covers a
    fresh install, and the DB covers types added through Lookup
    Maintenance after deployment."""
    from app.modules.system_admin.models import Lookup
    from app.modules.system_admin.services.lookup_service import registry
    from app.extensions import db

    known = {d.lookup_type for d in registry.definitions}
    known.update(
        row[0] for row in db.session.query(Lookup.lookup_type).distinct())
    return known


@bp.route("/reference/lookups", methods=["GET"])
@api_auth_required()
def reference_lookups(api_user):
    """Several lookup types in one request, keyed by type.

    Gated on auth only, matching /reference/departments, /vendors and
    /drivers. Was vehicle.view -- an accident of the Vehicle form being
    the first screen to need lookups. Lookups are general configuration
    several unrelated forms read (the Maintenance Order form needs
    ASSIGNMENT_CLASSIFICATION for a Vehicle Assignment Memo, which has
    nothing to do with vehicle master data).

    The Vehicle form needs four of these lists (FUEL_TYPE, TRANSMISSION,
    VEHICLE_BODY_TYPE, COMPONENT_GROUP). Four round-trips would render
    the four dropdowns at four different moments, each briefly empty.

    Uses get_by_type_with_fallback, as the Jinja route does for three of
    the four: on an unseeded install the code-registered defaults are
    returned instead of nothing. An empty Component Group dropdown does
    not read as "not seeded yet", it reads as "this fleet has no
    component groups".

    An unrecognised type is a 400, not an empty list. Silently returning
    {} for a typo would render a dropdown that looks exactly like
    unseeded master data, and the person would go configure something
    that was never the problem.
    """
    from app.modules.system_admin.services.lookup_service import LookupService

    raw = (request.args.get("types") or "").strip()
    if not raw:
        return _bad("types is required, e.g. ?types=FUEL_TYPE,TRANSMISSION")

    wanted = [t.strip().upper() for t in raw.split(",") if t.strip()]
    if not wanted:
        return _bad("types is required, e.g. ?types=FUEL_TYPE,TRANSMISSION")

    known = _known_lookup_types()
    unknown = [t for t in wanted if t not in known]
    if unknown:
        return _bad(f"Unknown lookup type(s): {', '.join(sorted(unknown))}.")

    service = LookupService()
    out = {}
    for lookup_type in wanted:
        rows = service.get_by_type_with_fallback(lookup_type)
        out[lookup_type] = [
            {"code": r.code, "description": r.description} for r in rows]
    return jsonify({"items": out})


# ── PM schedules ────────────────────────────────────────────────────────────

def _pm_label(schedule):
    """The RICHER of the two labels Flask uses.

    The server-rendered <option> (vehicle_form.html 196-206) shows
    brand/model or vehicle type, the maintenance type, the profile code
    and the intervals. The api_search response used to replace those
    options on the first change event shows only maintenance type and a
    compact interval — so in Flask the dropdown's own labels change once
    the user touches Brand. That inconsistency is not worth inheriting;
    this returns the fuller form in both cases.

    The interval matters most: several packages can exist for one
    vehicle (typically from an imported profile), and without it they
    are indistinguishable in the list.
    """
    if schedule.vehicle_brand:
        model = (schedule.vehicle_model_ref.name
                 if schedule.vehicle_model_ref else "")
        scope = f"{schedule.vehicle_brand.name} {model}".strip()
    elif schedule.vehicle_make:
        scope = f"{schedule.vehicle_make} {schedule.vehicle_model or ''}".strip()
    elif schedule.vehicle_type:
        scope = schedule.vehicle_type.name
    else:
        scope = "All Vehicles"

    parts = [f"{scope} — {schedule.maintenance_type.name}"]
    if schedule.profile_code:
        seq = (f" #{schedule.sequence_position}"
               if schedule.sequence_position else "")
        parts.append(f" [{schedule.profile_code}{seq}]")
    if schedule.interval_km:
        parts.append(f" — {schedule.interval_km:,} km")
    if schedule.interval_days:
        parts.append(f" / {schedule.interval_days} days")
    return "".join(parts)


@bp.route("/reference/pm-schedules", methods=["GET"])
@api_auth_required("vehicle.view")
def reference_pm_schedules(api_user):
    """PM Templates applicable to the criteria currently on the form.

    Delegates to PMScheduleService.list_applicable_for_criteria, which
    owns the matching precedence (FK brand+model → free-text make+model
    → vehicle type). No criteria returns nothing, as that service does:
    listing every active template regardless of relevance is a bug Flask
    already fixed once on this exact dropdown.

    `current_id` pins an existing assignment to the front of the list
    even when the criteria filter excludes it, mirroring vehicle_edit
    (routes.py 1170-1176). Without it, a deliberate manual override
    disappears from its own dropdown as soon as Brand is touched, and
    the next save clears it — the field looks blank because it IS blank,
    and nothing reports the loss.
    """
    from app.modules.maintenance_config.service import PMScheduleService

    try:
        vehicle_type_id = request.args.get("vehicle_type_id", type=int)
        current_id = request.args.get("current_id", type=int)
    except (TypeError, ValueError):
        return _bad("vehicle_type_id and current_id must be integers.")

    service = PMScheduleService()
    rows = service.list_applicable_for_criteria(
        brand_name=request.args.get("brand_name"),
        model_name=request.args.get("model_name"),
        vehicle_type_id=vehicle_type_id)

    if current_id and current_id not in [s.id for s in rows]:
        existing = service.get_by_id(current_id)
        if existing:
            rows = [existing] + list(rows)

    return jsonify({"items": [
        {"id": s.id, "label": _pm_label(s),
         "is_current": s.id == current_id} for s in rows]})


# ── Drivers and departments ─────────────────────────────────────────────────

@bp.route("/reference/drivers", methods=["GET"])
@api_auth_required()
def reference_drivers(api_user):
    """Driver search for the Assigned Driver selector.

    Gated on auth only, matching the reasoning already recorded on
    /reference/departments and /reference/vendors. Was vehicle.view --
    an accident of the Vehicle Assignment screen having been the first
    to need this list. Drivers are general reference data several
    unrelated forms need (vehicle assignment, ATD, and now Trip
    Tickets), and a trip clerk with no vehicle permissions could not
    otherwise populate the driver field on their own form.

    Only ACTIVE drivers, as the form states — offering a deactivated
    driver would assign accountability for a vehicle to someone who has
    left. Org scoping is DriverService's, passed the API user, so this
    cannot surface a driver the browser would hide.

    The label matches the Jinja option exactly:
    `{employee_number} — {last_name}, {first_name} ({license_type})`.
    That string is what the user reads back to confirm they picked the
    right person out of a roster with repeated surnames, so it has to be
    the same string in both apps.
    """
    from app.modules.master_data.driver.service import DriverService

    q = (request.args.get("q") or "").strip().lower()
    try:
        limit = int(request.args.get("limit", 20))
    except (TypeError, ValueError):
        return _bad("limit must be an integer.")
    limit = max(1, min(limit, 50))

    drivers = DriverService().list(include_inactive=False, user=api_user)
    if q:
        drivers = [
            d for d in drivers
            if q in (d.employee_number or "").lower()
            or q in (d.first_name or "").lower()
            or q in (d.last_name or "").lower()]

    return jsonify({"items": [
        {"id": d.id,
         "employee_number": d.employee_number,
         "label": (f"{d.employee_number} — {d.last_name}, {d.first_name} "
                   f"({d.license_type})")}
        for d in drivers[:limit]]})


@bp.route("/reference/departments", methods=["GET"])
@api_auth_required()
def reference_departments(api_user):
    """Departments for the assignment section.

    Gated on auth only, matching the same reasoning already recorded on
    /reference/vendors. Was vehicle.view -- an accident of whichever
    screen first needed this list, since Department is general
    org-structure reference data several forms need (vehicle
    assignment, drivers, and now Purchase Requests). A PR clerk with no
    vehicle permissions could not otherwise populate their own
    creation form's Department field, which is unrelated to vehicles
    at all.

    Carries branch_id and branch_name, and accepts an optional
    ?branch_id= filter -- the real hierarchy Department already has
    (branch_id has been a column on it all along; this was never a
    schema gap). Without either, a form with many departments across
    several branches shows duplicate-looking names with no way to tell
    them apart: "Fleet Operations" at Manila Hub and "Fleet Operations"
    at Cebu Hub are two different rows, and a flat list renders them
    identically.

    Filtering to one branch is the real fix, not merely labelling the
    duplicates: a client that knows which branch it cares about (the
    New Purchase Request form, once a branch is chosen) gets exactly
    that branch's departments, not the whole company's list to scan.
    """
    from app.modules.master_data.org.service import DepartmentService

    branch_id = request.args.get("branch_id", type=int)
    rows = DepartmentService().list()
    if branch_id is not None:
        rows = [d for d in rows if d.branch_id == branch_id]
    return jsonify({"items": [
        {"id": d.id, "code": d.code, "name": d.name,
         "branch_id": d.branch_id,
         "branch_name": d.branch.name if d.branch else None}
        for d in rows]})



@bp.route("/reference/vendors", methods=["GET"])
@api_auth_required()
def reference_vendors(api_user):
    """Vendor dropdown for tire/battery forms.

    Gated only on auth: the form needs the list to enrol stock; requiring
    vendor.view would empty the dropdown for storekeepers who can create
    tires but not maintain the vendor master.
    """
    from app.modules.master_data.vendor.service import VendorService
    rows = VendorService().list(include_inactive=False)
    return jsonify({"items": [
        {"id": v.id, "code": getattr(v, "code", None), "name": v.name}
        for v in rows]})


# ── Searchable pickers ──────────────────────────────────────────────────────
#
# Twelve dropdowns in React load their options with a hard page-size cap
# and render them into a plain <select>. At 5,000 vehicles the ATD form
# could select 100 of them; the rest were unreachable and nothing on
# screen said so, so the dropdown looked complete and a missing plate
# read as "not enrolled".
#
# These endpoints SEARCH instead of enumerating, so the cap stops being
# a cap on what is SELECTABLE and becomes only a cap on how many matches
# are shown at once -- and `has_more` makes even that visible.
#
# They are the bearer-token equivalent of Flask's api_search, which is
# @login_required and therefore unreachable from React. Same job, same
# labels, different auth.

#: Matches at once. Enough that a reasonable search resolves, few enough
#: that the list stays scannable. Paired with has_more, never silent.
_PICKER_LIMIT = 25


@bp.route("/reference/vehicles", methods=["GET"])
@api_auth_required("vehicle.view")
def reference_vehicles(api_user):
    """Vehicle picker, searched server-side.

    Matches plate, conduction number, brand and model, because a user
    reaches for whichever identifier they have to hand -- a plate-only
    search sends someone hunting for a conduction number they were
    handed on paper.

    No query returns a FIRST PAGE rather than nothing: an empty picker
    on focus reads as "no vehicles enrolled", and on a small fleet the
    vehicle is usually right there.

    DISPOSED vehicles are excluded. A retired vehicle must not be
    selectable on a new trip ticket or maintenance order; it stays
    visible in the register, which is a different question from "may I
    raise work against it".

    Scoping is VehicleService's, so the picker cannot offer a vehicle
    the caller could not open.
    """
    from app.modules.master_data.vehicle.service import VehicleService

    q = (request.args.get("q") or "").strip()

    # scope=assigned narrows to the caller's OWN vehicles.
    #
    # Requested by the field app, where a driver picking a vehicle for a
    # checklist should see the one they hold, not the branch. Opt-in
    # because this endpoint also serves trip tickets and maintenance
    # orders on the web, where org scope is correct.
    #
    # This is a PICKER convenience, not the control. A request built by
    # hand ignores it entirely, which is why the checklist create
    # endpoint checks assignment itself. Narrowing here only stops the
    # app offering something the server would refuse.
    if request.args.get("scope") == "assigned":
        from app.modules.user_management.assignee_scope_service import (
            AssigneeScopeService)
        ids = AssigneeScopeService().assigned_vehicle_ids(api_user)
        if not ids:
            return jsonify({"items": [], "has_more": False})
        rows = [v for v in VehicleService().list(user=api_user)
                if v.id in ids and v.status != "DISPOSED"]
        if q:
            ql = q.lower()
            rows = [v for v in rows
                    if ql in (v.plate_number or "").lower()
                    or ql in (v.conduction_number or "").lower()
                    or ql in (v.brand or "").lower()
                    or ql in (v.model or "").lower()]
        return jsonify({"items": [{
            "id": v.id,
            "label": (f"{v.plate_number or v.conduction_number} — "
                      f"{v.brand or ''} {v.model or ''}".strip()),
        } for v in rows], "has_more": False})

    # list_page filters and scopes in SQL. page_size is a DISPLAY cap
    # here, not a limit on what is reachable -- the search is what
    # reaches.
    rows, total = VehicleService().list_page(
        user=api_user, q=q or None, page=1, page_size=_PICKER_LIMIT,
        sort="plate", direction="asc", include_disposed=False)

    def label(v):
        # Plate first: it is what is painted on the vehicle and what
        # someone is holding a job sheet for. Falls back to the
        # conduction number, since a new vehicle has one before a plate
        # is issued and "— Toyota" would make every unplated vehicle
        # look identical.
        ident = v.plate_number or v.conduction_number or f"#{v.id}"
        make = " ".join(x for x in (v.brand, v.model) if x)
        year = f" ({v.year})" if v.year else ""
        return f"{ident} — {make}{year}" if make else ident

    return jsonify({
        "items": [
            {"id": v.id,
             "plate_number": v.plate_number,
             "conduction_number": v.conduction_number,
             "label": label(v),
             "branch": v.branch.name if v.branch else None,
             "status": v.status}
            for v in rows],
        # A cap on DISPLAY is fine; a cap that hides its own existence is
        # what the old dropdown did. This lets the picker say "keep
        # typing" instead of implying the list is complete.
        "has_more": total > len(rows),
        "total": total,
    })


@bp.route("/reference/branches", methods=["GET"])
@api_auth_required()
def reference_branches(api_user):
    """Branch picker.

    Gated on auth only, matching the reasoning already recorded on
    /reference/vendors: a form needs the list to be fillable, and
    requiring branch.view would empty the dropdown for everyone who
    works in a branch without maintaining the branch master.
    """
    from app.modules.master_data.org.service import BranchService

    q = (request.args.get("q") or "").strip().lower()
    rows = BranchService().list()
    if q:
        rows = [b for b in rows
                if q in (b.name or "").lower() or q in (b.code or "").lower()]
    return jsonify({
        "items": [{"id": b.id, "code": b.code, "name": b.name,
                   "label": f"{b.code} — {b.name}" if b.code else b.name}
                  for b in rows[:_PICKER_LIMIT]],
        "has_more": len(rows) > _PICKER_LIMIT,
        "total": len(rows),
    })


@bp.route("/reference/users", methods=["GET"])
@api_auth_required()
def reference_users(api_user):
    """User picker — notify-on-comment, approver override.

    INACTIVE users are excluded. Assigning work to, or notifying,
    someone who has left is the same fault as offering a deactivated
    driver: the record looks complete and the person never sees it.
    """
    from app.modules.user_management.models import User

    q = (request.args.get("q") or "").strip().lower()
    query = User.query.filter_by(is_active=True)
    rows = query.order_by(User.username).all()
    if q:
        rows = [u for u in rows
                if q in (u.username or "").lower()
                or q in (u.first_name or "").lower()
                or q in (u.last_name or "").lower()
                or q in (u.email or "").lower()]

    def label(u):
        full = " ".join(x for x in (u.first_name, u.last_name) if x)
        return f"{full} ({u.username})" if full else u.username

    return jsonify({
        "items": [{"id": u.id, "username": u.username, "label": label(u)}
                  for u in rows[:_PICKER_LIMIT]],
        "has_more": len(rows) > _PICKER_LIMIT,
        "total": len(rows),
    })
