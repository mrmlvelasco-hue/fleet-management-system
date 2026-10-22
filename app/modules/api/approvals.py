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


# =====================================================================
# Mobile Approval Inbox read model (2026-10-22)
# =====================================================================
#
# Source review before building the mobile Approval Inbox found two
# gaps the existing endpoints could not fill:
#
#   * /worklist/pending-approvals and /worklist/inbox carry only the
#     reference, document type, level and date. The mockup's card also
#     shows vehicle, branch, requester, amount and status -- filling those
#     from the phone would mean one detail request PER CARD, each gated
#     on a different module permission.
#   * Nothing returned what an approver has already APPROVED or
#     REJECTED, which the inbox's filter chips need.
#
# So this is a READ model over the engine's own tables, nothing more:
#
#   * PENDING items are exactly ApprovalTaskService.list_for_user() --
#     the same eligibility + org-scope + dedup the web worklist uses.
#     Not a second definition of "who may approve".
#   * Decided items are only the caller's OWN ApprovalAction rows. You
#     can always see a decision you made; you never see someone else's.
#   * Decisions still go through each module's existing
#     /approve | /reject | /return endpoint, where the engine re-checks
#     eligibility, level and state on every call.

_INBOX_FILTERS = ("all", "pending", "approved", "rejected")
_DECISIONS = ("APPROVE", "REJECT", "RETURN")
#: The document's own money field, in the order BaseTransactionService
#: resolves its matrix amount, plus or_cr_cost (registration). Tire and
#: battery are handled separately: their figure is cost + labor_cost.
_AMOUNT_FIELDS = ("amount", "estimated_cost", "actual_cost",
                  "total_amount", "or_cr_cost")


def _document_amount(row):
    """The amount shown on a card: the document's own figure, or None.

    Deliberately NOT ApprovalInstance.amount. That is the matrix-routing
    figure, and BaseTransactionService._approval_amount falls back to
    Decimal("0") when it finds none of its known fields -- every trip
    ticket and ATD (no money at all) and every tire/battery transaction
    (money kept in cost + labor_cost, which it does not read) is routed
    as 0. On a card that 0 would read as "this costs nothing".
    """
    if row is None:
        return None
    if hasattr(row, "cost") or hasattr(row, "labor_cost"):
        parts = [_num(getattr(row, f, None)) for f in ("cost", "labor_cost")]
        parts = [p for p in parts if p is not None]
        if parts:
            return sum(parts)
    for field in _AMOUNT_FIELDS:
        value = _num(getattr(row, field, None))
        if value is not None:
            return value
    return None


def _model_for_table(reference_table):
    """The model class mapped to a table, from SQLAlchemy's own registry.

    Generic on purpose: a new approvable module shows up here without
    this file being told about it -- the failure mode REGISTRY above and
    worklist._REACT_ROUTE_MAP have each hit when someone forgot to add a
    line."""
    from app.extensions import db
    for mapper in db.Model.registry.mappers:
        cls = mapper.class_
        if getattr(cls, "__tablename__", None) == reference_table:
            return cls
    return None


def _rows_by_ref(refs):
    """{(table, id): row} for every (table, id) pair, one query per table."""
    by_table = {}
    for table, rid in refs:
        by_table.setdefault(table, set()).add(rid)
    rows = {}
    for table, ids in by_table.items():
        model = _model_for_table(table)
        if model is None:
            continue
        for r in model.query.filter(model.id.in_(ids)).all():
            rows[(table, r.id)] = r
    return rows


def _iso(value):
    """ISO text; naive datetimes are marked UTC. Every timestamp here is
    stored naive in UTC (BaseModel.utcnow, ApprovalAction.acted_at), and
    a phone that receives it unmarked reads it as LOCAL time -- eight
    hours off in Manila, on every "Submitted" and "Pending for ..."."""
    from datetime import datetime, timezone
    if isinstance(value, datetime) and value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat() if hasattr(value, "isoformat") else value


def _level_label(instance):
    """"Fleet Manager" for the level awaiting action -- the engine's own
    label with its "Role: " / "User: " prefix removed, the same way the
    My Vehicle maintenance card shows it."""
    if instance is None or instance.status != "PENDING":
        return None
    path = instance.approval_path
    if path is None:
        return None
    from app.core.approval.engine import ApprovalEngine
    for level in path.levels:
        if level.level_number == instance.current_level:
            label = ApprovalEngine()._approver_label(level)
            for prefix in ("Role: ", "User: "):
                if label.startswith(prefix):
                    return label[len(prefix):]
            return label
    return None


def _card(instance, row, *, task=None, my_action=None):
    """One inbox card. Every value is read from the engine tables or the
    document itself; anything not recorded comes back as None so the
    phone omits it rather than showing an invented value."""
    from app.extensions import db
    from app.core.approval.models import ApprovalAction
    from app.modules.user_management.models import User
    from app.modules.api.worklist import _REACT_ROUTE_MAP

    vehicle = getattr(row, "vehicle", None) if row is not None else None
    plate = None
    vehicle_label = None
    if vehicle is not None:
        plate = (getattr(vehicle, "plate_number", None)
                 or getattr(vehicle, "conduction_number", None))
        vehicle_label = " ".join(
            str(x) for x in (getattr(vehicle, "brand", None),
                             getattr(vehicle, "model", None)) if x) or None

    branch = None
    if instance.branch_id:
        from app.modules.master_data.org.models import Branch
        b = db.session.get(Branch, instance.branch_id)
        branch = b.name if b else None
    if branch is None and vehicle is not None and getattr(
            vehicle, "branch", None):
        branch = vehicle.branch.name

    requester = None
    if instance.submitted_by:
        u = db.session.get(User, instance.submitted_by)
        requester = (u.full_name or u.username) if u else None

    submit = (ApprovalAction.query
              .filter_by(instance_id=instance.id, action="SUBMIT")
              .order_by(ApprovalAction.id.desc()).first())

    amount = _document_amount(row)

    doc_label = (instance.document_type.name
                 if instance.document_type else None)
    template = _REACT_ROUTE_MAP.get(instance.reference_table)
    card = {
        "reference_table": instance.reference_table,
        "reference_id": instance.reference_id,
        "document_label": doc_label,
        "document_number": (getattr(row, "document_number", None)
                            or (task.document_number if task else None)),
        "document_status": getattr(row, "status", None),
        # The APPROVAL's status -- PENDING / APPROVED / REJECTED /
        # RETURNED / CANCELLED -- which is what an approver filters by.
        "status": instance.status,
        "plate_number": plate,
        "vehicle_label": vehicle_label,
        "branch": branch,
        "requester": requester,
        "submitted_at": _iso(submit.acted_at) if submit else None,
        "amount": amount,
        "current_level": (instance.current_level
                          if instance.status == "PENDING" else None),
        "current_level_label": _level_label(instance),
        # When THIS level reached the approver -- what "Pending for 2
        # days" counts from. Not submitted_at: at level 2 the clock for
        # this approver started when level 1 signed.
        "pending_since": (_iso(task.created_at)
                          if task is not None and task.created_at else None),
        "my_action": my_action.action if my_action else None,
        "my_action_at": _iso(my_action.acted_at) if my_action else None,
        "my_remarks": my_action.remarks if my_action else None,
        "url": (template.format(id=instance.reference_id)
                if template else None),
    }
    return card


def _matches(card, q):
    if not q:
        return True
    hay = " ".join(str(card.get(k) or "") for k in (
        "document_number", "plate_number", "vehicle_label", "requester",
        "document_label", "branch")).lower()
    return q in hay


@bp.route("/approvals/inbox", methods=["GET"])
@api_auth_required()
def approval_inbox(api_user):
    """Cards for the mobile Approval Inbox.

    ?filter=all|pending|approved|rejected (default all), ?q= search over
    reference, plate, vehicle, requester, document type and branch.
    `counts` is computed BEFORE filtering, so the chips stay put while
    the list changes.
    """
    from app.core.approval.models import ApprovalAction, ApprovalInstance
    from app.core.approval.task_service import ApprovalTaskService

    which = (request.args.get("filter") or "all").lower()
    if which not in _INBOX_FILTERS:
        return jsonify({"message": f"Unknown filter '{which}'."}), 400
    q = (request.args.get("q") or "").strip().lower()

    tasks = ApprovalTaskService().list_for_user(api_user)

    # My own decisions, newest first, one per document (the latest).
    # Capped: this is an inbox, not an audit report.
    mine = (ApprovalAction.query
            .filter(ApprovalAction.acted_by == api_user.id,
                    ApprovalAction.action.in_(_DECISIONS))
            .order_by(ApprovalAction.id.desc())
            .limit(300).all())
    latest_by_instance = {}
    for a in mine:
        latest_by_instance.setdefault(a.instance_id, a)

    pending_keys = {(t.reference_table, t.reference_id) for t in tasks}
    instances = {}
    if latest_by_instance:
        for inst in ApprovalInstance.query.filter(
                ApprovalInstance.id.in_(list(latest_by_instance))).all():
            instances[inst.id] = inst

    refs = list(pending_keys) + [
        (i.reference_table, i.reference_id) for i in instances.values()]
    rows = _rows_by_ref(refs)

    cards = []
    for t in tasks:
        inst = t.instance
        if inst is None:
            continue
        c = _card(inst, rows.get((t.reference_table, t.reference_id)),
                  task=t)
        c["bucket"] = "pending"
        cards.append(c)

    for inst_id, action in latest_by_instance.items():
        inst = instances.get(inst_id)
        if inst is None:
            continue
        key = (inst.reference_table, inst.reference_id)
        # Back on my desk (returned and resubmitted to my level): it is a
        # pending task now, already listed above.
        if key in pending_keys:
            continue
        c = _card(inst, rows.get(key), my_action=action)
        c["bucket"] = {"APPROVE": "approved", "REJECT": "rejected"}.get(
            action.action, "returned")
        cards.append(c)

    cards = [c for c in cards if _matches(c, q)]
    counts = {
        "all": len(cards),
        "pending": sum(1 for c in cards if c["bucket"] == "pending"),
        "approved": sum(1 for c in cards if c["bucket"] == "approved"),
        "rejected": sum(1 for c in cards if c["bucket"] == "rejected"),
    }
    if which != "all":
        cards = [c for c in cards if c["bucket"] == which]

    # Pending first, oldest waiting at the top -- the one most overdue
    # for a decision. Decided items after, newest decision first.
    pending = sorted((c for c in cards if c["bucket"] == "pending"),
                     key=lambda c: c["pending_since"] or c["submitted_at"]
                     or "")
    decided = sorted((c for c in cards if c["bucket"] != "pending"),
                     key=lambda c: c["my_action_at"] or "", reverse=True)
    return jsonify({"items": pending + decided, "counts": counts,
                    "filter": which})


@bp.route("/approvals/<reference_table>/<int:reference_id>/card",
          methods=["GET"])
@api_auth_required()
def approval_card(api_user, reference_table, reference_id):
    """One card plus the approval chain and `can_act`, for the Approval
    Details screen's header and History tab.

    Visible to the caller only if the document is in their inbox -- an
    eligible pending approver, or someone who has recorded a decision on
    it. 404 otherwise (not 403): sequential ids must not become a way to
    learn which documents exist.
    """
    from app.core.approval.engine import ApprovalEngine
    from app.core.approval.models import ApprovalAction, ApprovalInstance
    from app.core.approval.task_service import ApprovalTaskService
    from app.modules.api.approval_eligibility import can_act_on

    if _model_for_table(reference_table) is None:
        return _not_found()
    inst = (ApprovalInstance.query
            .filter_by(reference_table=reference_table,
                       reference_id=reference_id)
            .order_by(ApprovalInstance.id.desc()).first())
    if inst is None:
        return _not_found()

    task = next((t for t in ApprovalTaskService().list_for_user(api_user)
                 if t.reference_table == reference_table
                 and t.reference_id == reference_id), None)
    mine = (ApprovalAction.query
            .filter(ApprovalAction.instance_id == inst.id,
                    ApprovalAction.acted_by == api_user.id,
                    ApprovalAction.action.in_(_DECISIONS))
            .order_by(ApprovalAction.id.desc()).first())
    if task is None and mine is None:
        return _not_found()

    row = _rows_by_ref([(reference_table, reference_id)]).get(
        (reference_table, reference_id))
    card = _card(inst, row, task=task, my_action=mine)
    engine = ApprovalEngine()
    card["approval_chain"] = [
        {**s, "acted_at": _iso(s.get("acted_at"))}
        for s in engine.get_approval_chain(inst)]
    card["can_act"] = can_act_on(inst, api_user, card["document_status"])
    card["is_final_level"] = bool(inst.is_final_level)
    return jsonify(card)
