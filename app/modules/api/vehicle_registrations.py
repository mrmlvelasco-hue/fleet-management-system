"""Vehicle Registration API.

Phase 1 scope in the project's master prompt, with only a read-only
history endpoint before this.

Every LTO rule stays in VehicleRegistrationService: the three-year /
one-year validity split, the duplicate-active-registration guard, the
"nothing to renew" guard, OR/CR uniqueness, the registration-date-
before-expiry sanity check, and assigning the plate to Vehicle Master
on completion. This layer validates, coerces and serialises, and
derives nothing of its own.

Notably validity_years is NOT accepted from the client on create: the
service derives it from registration_type, and a client able to choose
its own validity could silently record a non-compliant registration.
"""
from flask import jsonify, request

from app.modules.api.auth import api_auth_required
from app.modules.api.coercion import Coercer, FieldValueError
from app.modules.api.routes import bp

_COERCER = Coercer(
    ints={"vehicle_id": "Vehicle",
          "odometer_at_registration": "Odometer at Registration"},
    decimals={"or_cr_cost": "OR/CR Cost"},
    dates={"registration_date": "Registration Date"},
)


def _bad(message, field=None):
    return jsonify({"error": "validation", "message": message,
                    "fields": {field or "_": message}}), 400


def _not_found(label="Vehicle Registration"):
    return jsonify({"error": "not_found",
                    "message": f"{label} not found."}), 404


def _conflict(message):
    return jsonify({"error": "conflict", "message": message}), 409


def _iso(v):
    return v.isoformat() if v else None


def _money(v):
    return None if v is None else f"{v:.2f}"


def _reg_json(r, *, detail=False, api_user=None):
    data = {
        "id": r.id,
        "document_number": r.document_number,
        "vehicle_id": r.vehicle_id,
        "plate_number": (r.plate_number
                         or (r.vehicle.plate_number if r.vehicle else None)),
        "conduction_number": (r.vehicle.conduction_number
                              if r.vehicle else None),
        "vehicle_label": (
            " ".join(x for x in [getattr(r.vehicle, "brand", None),
                                 getattr(r.vehicle, "model", None)] if x)
            or None) if r.vehicle else None,
        "registration_type": r.registration_type,
        "or_number": r.or_number,
        "cr_number": r.cr_number,
        "registration_date": _iso(r.registration_date),
        "validity_years": r.validity_years,
        "expiry_date": _iso(r.expiry_date),
        "or_cr_cost": _money(r.or_cr_cost),
        "odometer_at_registration": r.odometer_at_registration,
        "status": r.status,
        "is_historical": r.is_historical,
        "requested_by": r.requester.username if r.requester else None,
    }
    if detail:
        from app.modules.api.company_letterhead import company_letterhead
        data["company"] = company_letterhead()
        data["checklist_items"] = [{
            "id": i.id,
            "activity_code": i.activity_code,
            "activity_description": i.activity_description,
            "is_done": i.is_done,
            "sort_order": i.sort_order,
        } for i in sorted(getattr(r, "checklist_items", None) or [],
                          key=lambda i: i.sort_order)]
        # Same gap found and fixed on Trip Tickets: React's
        # ApprovalWorkflow/MoDecisionPanel were already wired to read
        # these fields, but this serializer never populated any of
        # them, so the approval panel always rendered as "not yet
        # submitted" regardless of the registration's real state.
        # Matches purchase_requests.py's identical block.
        from app.core.approval.engine import ApprovalEngine
        inst = getattr(r, "approval_instance", None)
        engine = ApprovalEngine()
        chain = []
        if inst is not None:
            for entry in engine.get_approval_chain(inst):
                acted_at = entry.get("acted_at")
                chain.append({
                    "level_number": entry.get("level_number"),
                    "approver_label": entry.get("approver_label"),
                    "status": entry.get("status"),
                    "acted_by_name": entry.get("acted_by_name"),
                    "acted_at": acted_at.isoformat() if acted_at else None,
                    "remarks": entry.get("remarks"),
                })
            data["approval_instance_status"] = inst.status
            data["approval_current_level"] = inst.current_level
            data["can_act"] = bool(
                api_user and engine.is_eligible_approver(inst, api_user))
        else:
            data["approval_instance_status"] = None
            data["approval_current_level"] = None
            data["can_act"] = False
        data["approval_chain"] = chain
        data["has_approval_instance"] = inst is not None
        data["is_requester"] = bool(
            api_user is not None and r.requested_by == getattr(api_user, "id", None))
    return data


@bp.route("/vehicle-registrations", methods=["GET"])
@api_auth_required("vehicleregistration.view")
def list_vehicle_registrations(api_user):
    from app.modules.transactions.vehicle_registration.service import (
        VehicleRegistrationService)

    page = request.args.get("page", 1, type=int)
    page_size = min(request.args.get("page_size", 25, type=int), 100)
    rows, pagination = VehicleRegistrationService().list_filtered(
        user=api_user, search=request.args.get("q") or None,
        status=request.args.get("status") or None,
        page=page, per_page=page_size)
    return jsonify({
        "items": [_reg_json(r) for r in rows],
        "total": pagination.total, "page": page, "page_size": page_size,
        "pages": pagination.pages,
    })


@bp.route("/vehicle-registrations/expiring", methods=["GET"])
@api_auth_required("vehicleregistration.view")
def expiring_vehicle_registrations(api_user):
    """Backs the dashboard's "Registration Renewals" tile, which has
    had no real destination in React because this did not exist.

    Reuses the service's own get_expiring_registrations -- the same one
    the Jinja dashboard uses -- so the two apps cannot disagree about
    which vehicles need attention.
    """
    from app.modules.transactions.vehicle_registration.service import (
        VehicleRegistrationService)

    days_ahead = request.args.get("days_ahead", 30, type=int)
    rows = VehicleRegistrationService().get_expiring_registrations(
        days_ahead=days_ahead)
    return jsonify({"items": [{
        "registration_id": e["registration"].id,
        "document_number": e["registration"].document_number,
        "vehicle_id": e["vehicle"].id if e["vehicle"] else None,
        "plate_number": (e["vehicle"].plate_number if e["vehicle"] else None),
        "conduction_number": (e["vehicle"].conduction_number
                              if e["vehicle"] else None),
        "expiry_date": _iso(e["registration"].expiry_date),
        "days_remaining": e["days_remaining"],
    } for e in rows]})


@bp.route("/vehicle-registrations", methods=["POST"])
@api_auth_required("vehicleregistration.create")
def create_vehicle_registration(api_user):
    from app.modules.transactions.vehicle_registration.service import (
        DuplicateActiveRegistrationError, NoExistingRegistrationError,
        VehicleRegistrationService)

    payload = request.get_json(silent=True) or {}
    try:
        fields = _COERCER.fields(payload, [
            "vehicle_id", "odometer_at_registration", "or_cr_cost",
            "registration_date"])
    except FieldValueError as exc:
        return _bad(str(exc), exc.field)

    if not fields.get("vehicle_id"):
        return _bad("Vehicle is required.", "vehicle_id")
    reg_type = (payload.get("registration_type") or "").upper()
    if reg_type not in ("NEW", "RENEWAL"):
        return _bad("Registration Type must be NEW or RENEWAL.",
                    "registration_type")
    if not fields.get("registration_date"):
        return _bad("Registration Date is required.", "registration_date")

    try:
        reg = VehicleRegistrationService().create(
            vehicle_id=fields["vehicle_id"], registration_type=reg_type,
            registration_date=fields["registration_date"], user=api_user,
            or_cr_cost=fields.get("or_cr_cost"),
            odometer_at_registration=fields.get("odometer_at_registration"))
    except (DuplicateActiveRegistrationError,
            NoExistingRegistrationError) as exc:
        return _conflict(str(exc))
    except Exception as exc:
        return _conflict(str(exc))
    return jsonify(_reg_json(reg, detail=True, api_user=api_user)), 201


@bp.route("/vehicle-registrations/<int:rid>", methods=["GET"])
@api_auth_required("vehicleregistration.view")
def vehicle_registration_detail(api_user, rid):
    from app.modules.transactions.vehicle_registration.models import (
        VehicleRegistration)

    reg = VehicleRegistration.query.filter_by(id=rid).first()
    if reg is None:
        return _not_found()
    return jsonify(_reg_json(reg, detail=True, api_user=api_user))


@bp.route("/vehicle-registrations/<int:rid>/complete", methods=["POST"])
@api_auth_required("vehicleregistration.update")
def complete_vehicle_registration(api_user, rid):
    """Records the issued OR/CR and, for a newly plated unit, assigns
    the plate to Vehicle Master -- the moment a conduction-numbered
    vehicle becomes a plated one, per the master prompt's "Conduction
    Number before Plate Number"."""
    from app.modules.transactions.vehicle_registration.models import (
        VehicleRegistration)
    from app.modules.transactions.vehicle_registration.service import (
        DuplicateCRNumberError, DuplicateORNumberError,
        RegistrationDateOrderError, VehicleRegistrationService)

    if VehicleRegistration.query.filter_by(id=rid).first() is None:
        return _not_found()

    payload = request.get_json(silent=True) or {}
    try:
        VehicleRegistrationService().complete(
            rid,
            or_number=(payload.get("or_number") or "").strip() or None,
            cr_number=(payload.get("cr_number") or "").strip() or None,
            plate_number=(payload.get("plate_number") or "").strip() or None)
    except (DuplicateORNumberError, DuplicateCRNumberError,
            RegistrationDateOrderError) as exc:
        return _conflict(str(exc))
    except Exception as exc:
        return _conflict(str(exc))

    fresh = VehicleRegistration.query.filter_by(id=rid).first()
    return jsonify(_reg_json(fresh, detail=True, api_user=api_user))


def _lifecycle(api_user, rid, method_name):
    from app.modules.transactions.vehicle_registration.models import (
        VehicleRegistration)
    from app.modules.transactions.vehicle_registration.service import (
        VehicleRegistrationService)

    if VehicleRegistration.query.filter_by(id=rid).first() is None:
        return _not_found()
    p = request.get_json(silent=True) or {}
    try:
        if method_name == "submit":
            # submit() takes (record_id, user) only -- see
            # purchase_requests.py's _lifecycle_action for the full
            # story. Forwarding remarks unconditionally raised
            # "submit() got an unexpected keyword argument 'remarks'"
            # on every submit call.
            VehicleRegistrationService().submit(rid, user=api_user)
        else:
            getattr(VehicleRegistrationService(), method_name)(
                rid, user=api_user, remarks=p.get("remarks"))
    except Exception as exc:
        return _conflict(str(exc))
    fresh = VehicleRegistration.query.filter_by(id=rid).first()
    return jsonify(_reg_json(fresh, detail=True, api_user=api_user))


@bp.route("/vehicle-registrations/<int:rid>/submit", methods=["POST"])
@api_auth_required("vehicleregistration.update")
def submit_vehicle_registration(api_user, rid):
    return _lifecycle(api_user, rid, "submit")


@bp.route("/vehicle-registrations/<int:rid>/approve", methods=["POST"])
@api_auth_required("vehicleregistration.view")
def approve_vehicle_registration(api_user, rid):
    return _lifecycle(api_user, rid, "approve")


@bp.route("/vehicle-registrations/<int:rid>/reject", methods=["POST"])
@api_auth_required("vehicleregistration.view")
def reject_vehicle_registration(api_user, rid):
    return _lifecycle(api_user, rid, "reject")


@bp.route("/vehicle-registrations/<int:rid>/return", methods=["POST"])
@api_auth_required("vehicleregistration.view")
def return_vehicle_registration(api_user, rid):
    """Was entirely missing, same as Trip Tickets: without this route a
    registration could never reach RETURNED via the API, which also
    made resubmit below unreachable in practice."""
    return _lifecycle(api_user, rid, "return_document")


@bp.route("/vehicle-registrations/<int:rid>/resubmit", methods=["POST"])
@api_auth_required("vehicleregistration.update")
def resubmit_vehicle_registration(api_user, rid):
    """resubmit() is on BaseTransactionService, same as every other
    module. Also entirely missing before this fix."""
    return _lifecycle(api_user, rid, "resubmit")


@bp.route("/vehicle-registrations/<int:rid>/cancel", methods=["POST"])
@api_auth_required("vehicleregistration.update")
def cancel_vehicle_registration(api_user, rid):
    return _lifecycle(api_user, rid, "cancel")


@bp.route("/vehicle-registrations/<int:rid>/checklist/<int:item_id>",
         methods=["POST"])
@api_auth_required("vehicleregistration.update")
def toggle_registration_checklist(api_user, rid, item_id):
    from app.modules.transactions.vehicle_registration.models import (
        VehicleRegistration, RegistrationTransactionChecklistItem)
    from app.modules.transactions.vehicle_registration.service import (
        VehicleRegistrationService)

    item = RegistrationTransactionChecklistItem.query.filter_by(
        id=item_id).first()
    # The URL asserts the item belongs to this registration; the
    # service's toggle takes only an item id and does not check, so the
    # relationship is verified here rather than trusted.
    if item is None or item.registration_id != rid:
        return _not_found("Checklist item")

    payload = request.get_json(silent=True) or {}
    VehicleRegistrationService().toggle_checklist_item(
        item_id, bool(payload.get("done")), user=api_user)

    fresh = VehicleRegistration.query.filter_by(id=rid).first()
    return jsonify(_reg_json(fresh, detail=True, api_user=api_user))
