"""Module-agnostic approval endpoints.

The master prompt requires the Approval Engine to be reusable across all
modules and approval logic never to be hardcoded. That holds in the
SERVICE layer already -- each module's _approval_action is a shared
helper over the same ApprovalEngine -- but the API layer re-hardcodes it
once per module: /maintenance-orders/<id>/approve,
/purchase-requests/<id>/approve, /atd/<id>/approve, and so on.

That is affordable for the web frontend, which ships when we say so. It
is not affordable for the mobile app: a client that must know every
module by name needs a new App Store build every time a transaction type
is added. These two routes let the app know APPROVALS rather than
modules, so a new document type appears in the inbox and is actionable
with no rebuild.

This is NOT a second engine. Every action here resolves to the same
per-module service method the existing routes call, so an approval made
from a phone and one made from the web cannot diverge. The only thing
added is the lookup from reference_table to that service.

Online only, by decision: an approval is a signature. Queuing one on a
device and replaying it later would mean the approver's name lands on a
document whose state may have moved on underneath them -- the value
approved, the level, even whether it is still their decision. A
checklist is the driver's own observation and can wait for signal; an
approval cannot.
"""
from dataclasses import dataclass
from typing import Any, Callable, Optional

from flask import jsonify, request

from app.modules.api.auth import api_auth_required
from app.modules.api.routes import bp


@dataclass
class ApprovalEntry:
    """One approvable document type."""
    label: str
    permission: str
    model: Any
    service: Any
    #: Builds the label/value pairs the approver reads before deciding.
    detail_fn: Callable[[Any], list]
    amount_attr: Optional[str] = None


def _mo_entry():
    from app.modules.transactions.maintenance_order.models import (
        MaintenanceOrder)
    from app.modules.transactions.maintenance_order.service import (
        MaintenanceOrderService)

    def details(o):
        return _pairs([
            ("Vehicle", _vehicle_label(o)),
            ("Maintenance Type",
             getattr(o.maintenance_type, "name", None)
             if getattr(o, "maintenance_type", None) else None),
            ("Transaction Type",
             getattr(o.transaction_type, "name", None)
             if getattr(o, "transaction_type", None) else None),
            ("Scheduled", _d(o.scheduled_date)),
            ("Odometer at Service", o.odometer_at_service),
            ("Description", o.description),
        ])

    return ApprovalEntry(
        label="Maintenance Order", permission="maintenanceorder.view",
        model=MaintenanceOrder, service=MaintenanceOrderService,
        detail_fn=details, amount_attr="estimated_cost")


def _pr_entry():
    from app.modules.transactions.purchase_request.models import (
        PurchaseRequest)
    from app.modules.transactions.purchase_request.service import (
        PurchaseRequestService)

    def details(p):
        return _pairs([
            ("Vehicle", _vehicle_label(p)),
            ("Needed By", _d(getattr(p, "needed_by_date", None))),
            ("Vendor",
             getattr(p.vendor, "name", None)
             if getattr(p, "vendor", None) else None),
            ("Line Items", len(getattr(p, "lines", []) or [])),
            ("Purpose", getattr(p, "purpose", None)),
        ])

    return ApprovalEntry(
        label="Purchase Request", permission="purchaserequest.view",
        model=PurchaseRequest, service=PurchaseRequestService,
        detail_fn=details, amount_attr="amount")


#: Hand-maintained, exactly like worklist._REACT_ROUTE_MAP -- and that
#: map has already drifted once here, leaving a built PR screen
#: unreachable for two commits. test_every_registered_table_resolves
#: asserts each entry imports and points at a real model, so a typo
#: fails in CI rather than as a 500 on a phone in the field.
#:
#: Entries are FUNCTIONS so the imports stay lazy: importing every
#: transaction model at module load would pull most of the app into any
#: process that touches the API blueprint.

def _tt_entry():
    from app.modules.transactions.trip_ticket.models import TripTicket
    from app.modules.transactions.trip_ticket.service import (
        TripTicketService)

    def details(t):
        return _pairs([
            ("Vehicle", _vehicle_label(t)),
            ("Driver",
             getattr(t.driver, "full_name", None)
             if getattr(t, "driver", None) else
             getattr(t, "driver_name", None)),
            ("Date", _d(getattr(t, "trip_date", None))),
            ("Destination", getattr(t, "destination", None)),
            ("Purpose", getattr(t, "purpose", None)),
        ])

    return ApprovalEntry(
        label="Trip Ticket", permission="tripticket.view",
        model=TripTicket, service=TripTicketService, detail_fn=details)


def _atd_entry():
    from app.modules.transactions.atd.models import AuthorityToDrive
    from app.modules.transactions.atd.service import ATDService

    def details(a):
        return _pairs([
            ("Vehicle", _vehicle_label(a)),
            ("Driver",
             getattr(a.driver, "full_name", None)
             if getattr(a, "driver", None) else None),
            ("Valid From", _d(getattr(a, "valid_from", None))),
            ("Valid To", _d(getattr(a, "valid_to", None))),
            ("Purpose", getattr(a, "purpose", None)),
        ])

    return ApprovalEntry(
        label="Authority To Drive", permission="atd.view",
        model=AuthorityToDrive, service=ATDService, detail_fn=details)


def _vm_entry():
    from app.modules.transactions.vehicle_movement.models import (
        VehicleMovement)
    from app.modules.transactions.vehicle_movement.service import (
        VehicleMovementService)

    def details(m):
        return _pairs([
            ("Vehicle", _vehicle_label(m)),
            ("Movement Type", getattr(m, "movement_type", None)),
            ("Effective", _d(getattr(m, "effective_date", None))),
            ("Reason", getattr(m, "reason", None)),
        ])

    return ApprovalEntry(
        label="Vehicle Movement", permission="vehiclemovement.view",
        model=VehicleMovement, service=VehicleMovementService,
        detail_fn=details)


REGISTRY = {
    "maintenance_orders": _mo_entry,
    "purchase_requests": _pr_entry,
    # Added after a client screenshot of "For Your Action" in which
    # Trip Ticket and ATD rows could be read but not acted on. Their
    # detail screens had shipped; only these registrations were
    # missing, so the generic approve/reject/return endpoint refused
    # the reference_table and the card offered no buttons.
    "trip_tickets": _tt_entry,
    "authority_to_drives": _atd_entry,
    "vehicle_movements": _vm_entry,
}

#: Only the engine's own decisions are dispatchable. Without this
#: allow-list the action name reaches getattr() on the service and every
#: public method on it becomes callable over HTTP.
_ACTIONS = {
    "approve": "approve",
    "reject": "reject",
    "return": "return_document",
}


def _pairs(items):
    """Drop empties so the phone shows a short card, not a wall of
    dashes. An approver scrolling past six blank rows to reach the
    figure is the reason mobile approval screens get ignored."""
    return [{"label": k, "value": str(v)}
            for k, v in items if v not in (None, "", [])]


def _vehicle_label(row):
    v = getattr(row, "vehicle", None)
    if v is None:
        return None
    plate = getattr(v, "plate_number", None) or getattr(
        v, "conduction_number", None)
    brand = " ".join(str(x) for x in [getattr(v, "brand", None),
                                      getattr(v, "model", None)] if x)
    return " — ".join(x for x in [plate, brand] if x) or None


def _d(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


def _num(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def resolve_entry(reference_table):
    """The registered entry for a table, or None if it is not one we
    carry. None rather than an exception: the mobile client sends back
    whatever the inbox gave it, and an unrecognised table is "no such
    thing", not a server fault."""
    factory = REGISTRY.get(reference_table)
    if factory is None:
        return None
    return factory()


def _not_found():
    return jsonify({"message": "Not found."}), 404


def _conflict(message):
    return jsonify({"message": message}), 409


def _load(reference_table, reference_id):
    entry = resolve_entry(reference_table)
    if entry is None:
        return None, None
    row = entry.model.query.filter_by(id=reference_id).first()
    return entry, row


def _summary(entry, row, reference_table):
    return {
        "reference_table": reference_table,
        "reference_id": row.id,
        "document_number": getattr(row, "document_number", None),
        "document_label": entry.label,
        "status": getattr(row, "status", None),
        "amount": _num(getattr(row, entry.amount_attr, None)
                       if entry.amount_attr else None),
        "requested_by": getattr(row, "requested_by", None) or getattr(
            getattr(row, "requester", None), "full_name", None),
        "created_at": _d(getattr(row, "created_at", None)),
        "details": entry.detail_fn(row),
    }


@bp.route("/approvals/<reference_table>/<int:reference_id>/summary",
          methods=["GET"])
@api_auth_required()
def approval_summary(api_user, reference_table, reference_id):
    """The card an approver decides from, in a shape that is the same
    for every document type -- which is what lets one mobile screen
    serve all of them."""
    entry, row = _load(reference_table, reference_id)
    if entry is None or row is None:
        return _not_found()
    if not api_user.has_permission(entry.permission):
        return jsonify({"message": "Forbidden."}), 403
    return jsonify(_summary(entry, row, reference_table))


@bp.route("/approvals/<reference_table>/<int:reference_id>/<action>",
          methods=["POST"])
@api_auth_required()
def approval_action(api_user, reference_table, reference_id, action):
    method_name = _ACTIONS.get(action)
    if method_name is None:
        return jsonify({"message": f"Unknown approval action '{action}'."}), 400

    entry, row = _load(reference_table, reference_id)
    if entry is None or row is None:
        return _not_found()

    # The module's OWN permission, not a new approval-wide one.
    # Otherwise this endpoint is a way around every per-module
    # permission in the system.
    if not api_user.has_permission(entry.permission):
        return jsonify({"message": "Forbidden."}), 403

    payload = request.get_json(silent=True) or {}
    remarks = (payload.get("remarks") or "").strip()
    # A return with no reason tells the requester only that something is
    # wrong and leaves them guessing what. Enforced here rather than in
    # the client because the client is not the only caller.
    if action == "return" and not remarks:
        return jsonify({
            "message": "Comments are required when returning a document "
                       "to the initiator."}), 400

    try:
        getattr(entry.service(), method_name)(
            reference_id, user=api_user, remarks=remarks or None)
    except Exception as e:  # noqa: BLE001 -- engine refusals are 409
        # The request was well formed and the DOCUMENT is in the wrong
        # state, or the caller is not the eligible approver. Calling
        # that a bad request sends someone checking a payload that is
        # fine.
        return _conflict(str(e))

    entry, row = _load(reference_table, reference_id)
    return jsonify(_summary(entry, row, reference_table))
