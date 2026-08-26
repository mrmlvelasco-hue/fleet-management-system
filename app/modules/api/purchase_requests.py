"""Purchase Request API.

The module the API had zero endpoints for. Confirmed against Flask's
real model, form, list and detail templates before writing anything --
a client mockup for this module showed eight fields (category, priority,
branch, linked-MO-as-a-field, vehicle, VAT, part number, unit of
measure) that do not exist anywhere in Flask. None of them are built
here; see tests/unit/test_api_purchase_requests.py's own docstring for
the full audit.

Every rule stays in PurchaseRequestService: numbering, amount recompute,
the DRAFT-only line-editing guard, and the shared submit/approve/reject/
return/cancel lifecycle it inherits from BaseTransactionService --
already proven correct for Maintenance Orders. The API computes no
totals of its own.
"""
from flask import jsonify, request

from app.modules.api.auth import api_auth_required
from app.modules.api.coercion import Coercer, FieldValueError
from app.modules.api.routes import bp

_HEADER_COERCER = Coercer(
    ints={"department_id": "Department", "vendor_id": "Preferred Supplier"},
    dates={"needed_by_date": "Needed By Date"},
)
_HEADER_FIELDS = ["description", "department_id", "vendor_id",
                  "justification", "needed_by_date"]

_LINE_COERCER = Coercer(
    decimals={"quantity": "Quantity", "unit_cost": "Unit Cost"},
)


def _bad(message, field=None):
    return jsonify({"error": "validation", "message": message,
                    "fields": {field or "_": message}}), 400


def _not_found(label="Purchase Request"):
    return jsonify({"error": "not_found",
                    "message": f"{label} not found."}), 404


def _conflict(message):
    return jsonify({"error": "conflict", "message": message}), 409


def _money(v):
    return None if v is None else f"{v:.2f}"


def _line_json(line):
    return {
        "id": line.id,
        "item_description": line.item_description,
        "quantity": _money(line.quantity),
        "unit_cost": _money(line.unit_cost),
        "line_total": _money(line.line_total),
    }


def _linked_mo(pr_id):
    """The mockup's "Linked MO" field, derived correctly.

    PurchaseRequest has no column pointing at a MaintenanceOrder --
    MaintenanceOrder.purchase_request_id points the OTHER way, with no
    backref. So this is a query for the order referencing this PR, not
    a field read off the PR itself.
    """
    from app.modules.transactions.maintenance_order.models import (
        MaintenanceOrder)
    return MaintenanceOrder.query.filter_by(purchase_request_id=pr_id).first()


def _pr_json(pr, *, detail=False):
    data = {
        "id": pr.id,
        "document_number": pr.document_number,
        "description": pr.description,
        "department": pr.department.name if pr.department else None,
        "department_id": pr.department_id,
        "vendor": pr.vendor.name if pr.vendor else None,
        "vendor_id": pr.vendor_id,
        "justification": pr.justification,
        "needed_by_date": (pr.needed_by_date.isoformat()
                           if pr.needed_by_date else None),
        "amount": _money(pr.amount),
        "status": pr.status,
        "requested_by": pr.requester.username if pr.requester else None,
        # Locked once submitted, matching LineManagementError's own
        # rule. Sent as a decision so the client disables entry rather
        # than letting someone type a line that will be refused.
        "locked": pr.approval_instance_id is not None or pr.status != "DRAFT",
    }
    if detail:
        data["lines"] = [_line_json(l) for l in pr.lines]
        mo = _linked_mo(pr.id)
        data["linked_mo_id"] = mo.id if mo else None
        data["linked_mo_document_number"] = mo.document_number if mo else None
    return data


@bp.route("/purchase-requests", methods=["GET"])
@api_auth_required("purchaserequest.view")
def list_purchase_requests(api_user):
    from app.modules.transactions.purchase_request.service import (
        PurchaseRequestService)

    page = request.args.get("page", 1, type=int)
    page_size = min(request.args.get("page_size", 25, type=int), 100)
    q = request.args.get("q")
    status = request.args.get("status")

    rows, pagination = PurchaseRequestService().list_filtered(
        user=api_user, search=q or None, status=status or None,
        page=page, per_page=page_size)
    return jsonify({
        "items": [_pr_json(pr) for pr in rows],
        "total": pagination.total, "page": page, "page_size": page_size,
        "pages": pagination.pages,
    })


@bp.route("/purchase-requests", methods=["POST"])
@api_auth_required("purchaserequest.create")
def create_purchase_request(api_user):
    """A Purchase Request, with its lines created up front -- the
    service's own contract, unlike invoices where the header exists
    before lines can reference it. At least one line is required: a
    PR requesting nothing is not a real request.
    """
    from app.modules.transactions.purchase_request.service import (
        PurchaseRequestService)

    payload = request.get_json(silent=True) or {}
    try:
        fields = _HEADER_COERCER.fields(payload, _HEADER_FIELDS)
    except FieldValueError as exc:
        return _bad(str(exc), exc.field)

    if not fields.get("description"):
        return _bad("Description is required.", "description")
    if not fields.get("justification"):
        return _bad("Justification is required.", "justification")

    raw_lines = payload.get("lines") or []
    if not raw_lines:
        return _bad("At least one line item is required.", "lines")

    lines = []
    for i, raw in enumerate(raw_lines):
        if not raw.get("item_description"):
            return _bad(f"Line {i + 1}: description is required.", "lines")
        try:
            coerced = _LINE_COERCER.fields(raw, ["quantity", "unit_cost"])
        except FieldValueError as exc:
            return _bad(f"Line {i + 1}: {exc}", "lines")
        lines.append({"item_description": raw["item_description"],
                      "quantity": coerced.get("quantity", 1),
                      "unit_cost": coerced.get("unit_cost", 0)})

    # Optional MO link -- the "New Purchase Request" entry point from
    # the MO screen. Not a PurchaseRequestService.create() argument
    # (there is no maintenance_order_id column on PR; the link is
    # MaintenanceOrder.purchase_request_id, set from the OTHER side),
    # so it is validated and applied here, after a successful create,
    # mirroring generate_purchase_request's own two guards exactly:
    # an order may not already have a PR, and the order must exist.
    mo_id = payload.get("maintenance_order_id")
    order = None
    if mo_id is not None:
        from app.modules.transactions.maintenance_order.models import (
            MaintenanceOrder)
        try:
            mo_id = int(mo_id)
        except (TypeError, ValueError):
            return _bad("Maintenance Order id must be a number.",
                       "maintenance_order_id")
        order = MaintenanceOrder.query.filter_by(id=mo_id).first()
        if order is None:
            return _bad("That Maintenance Order was not found.",
                       "maintenance_order_id")
        if order.purchase_request_id:
            return _conflict(
                "This Maintenance Order already has a Purchase Request.")

    try:
        pr = PurchaseRequestService().create(user=api_user, lines=lines,
                                             **fields)
        if order is not None:
            order.purchase_request_id = pr.id
            from app.extensions import db
            db.session.commit()
            # Reproduced directly: without this, a caller that had
            # already read `order` earlier in the SAME session (which
            # this endpoint's own caller may do, and which this
            # project's test harness does across multiple client.*()
            # calls sharing one app context) sees `order.purchase_request`
            # as still None afterward -- the FK column updates
            # correctly, but a previously-lazy-loaded relationship on
            # an already-identity-mapped object does not silently
            # re-resolve itself just because a commit happened
            # elsewhere. expire() forces the NEXT access to re-query
            # rather than trust a cache that predates this write.
            db.session.expire(order, ["purchase_request"])
    except Exception as exc:
        return _conflict(str(exc))
    return jsonify(_pr_json(pr, detail=True)), 201


@bp.route("/purchase-requests/<int:pid>", methods=["GET"])
@api_auth_required("purchaserequest.view")
def purchase_request_detail(api_user, pid):
    from app.modules.transactions.purchase_request.models import (
        PurchaseRequest)

    pr = PurchaseRequest.query.filter_by(id=pid).first()
    if pr is None:
        return _not_found()
    data = _pr_json(pr, detail=True)
    _attach_approval_chain(data, pr, api_user)
    # Letterhead for the print view -- from System Administration's
    # Company Profile, matching Flask's purchaserequest_print.html.
    from app.modules.api.company_letterhead import company_letterhead
    data["company"] = company_letterhead()
    return jsonify(data)


def _attach_approval_chain(data, pr, api_user):
    """Same shape Maintenance Orders already attach
    (app/modules/api/maintenance_orders.py::_attach_approval_chain),
    duplicated rather than shared under this build's time budget --
    both read requester/approval_instance off their own model, which
    differ enough (MaintenanceOrder vs PurchaseRequest) that extracting
    a common helper safely wants its own pass rather than a rushed one
    here. Flagged so it does not look like an oversight.

    is_requester and has_approval_instance are DECISIONS from the
    server, matching Flask's own button conditions exactly:

      Submit         not approval_instance
      Mark Ordered   approval_instance.status == APPROVED and
                     status == DRAFT
      Mark Received  status == ORDERED
      Cancel         status not in (RECEIVED, CANCELLED) and requester
    """
    from app.core.approval.engine import ApprovalEngine

    inst = getattr(pr, "approval_instance", None)
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
        api_user is not None
        and getattr(pr, "requested_by", None) == getattr(api_user, "id", None))
    return data


@bp.route("/purchase-requests/<int:pid>/lines", methods=["POST"])
@api_auth_required("purchaserequest.update")
def add_purchase_request_line(api_user, pid):
    from app.modules.transactions.purchase_request.service import (
        LineManagementError, PurchaseRequestService)

    payload = request.get_json(silent=True) or {}
    if not payload.get("item_description"):
        return _bad("Description is required.", "item_description")
    try:
        fields = _LINE_COERCER.fields(payload, ["quantity", "unit_cost"])
    except FieldValueError as exc:
        return _bad(str(exc), exc.field)

    try:
        pr = PurchaseRequestService().add_line(
            pid, item_description=payload["item_description"],
            quantity=fields.get("quantity", 1),
            unit_cost=fields.get("unit_cost", 0))
    except LineManagementError as exc:
        return _conflict(str(exc))
    except Exception as exc:
        return _conflict(str(exc))
    if pr is None:
        return _not_found()
    return jsonify(_pr_json(pr, detail=True)), 201


@bp.route("/purchase-requests/<int:pid>/lines/<int:line_id>",
         methods=["DELETE"])
@api_auth_required("purchaserequest.update")
def remove_purchase_request_line(api_user, pid, line_id):
    from app.modules.transactions.purchase_request.models import (
        PurchaseRequest, PurchaseRequestLine)
    from app.modules.transactions.purchase_request.service import (
        LineManagementError, PurchaseRequestService)

    # The service's remove_line takes only a line id, same shape as
    # invoices' remove_line, and the same ownership gap applies: the
    # URL asserts a relationship the service does not verify.
    line = PurchaseRequestLine.query.filter_by(id=line_id).first()
    if line is None or line.pr_id != pid:
        return _not_found("Purchase Request line")

    try:
        PurchaseRequestService().remove_line(line_id)
    except LineManagementError as exc:
        return _conflict(str(exc))

    pr = PurchaseRequest.query.filter_by(id=pid).first()
    return jsonify(_pr_json(pr, detail=True))


def _lifecycle_action(api_user, pid, method_name):
    from app.modules.transactions.purchase_request.models import (
        PurchaseRequest)
    from app.modules.transactions.purchase_request.service import (
        PurchaseRequestService)

    svc = PurchaseRequestService()
    if PurchaseRequest.query.filter_by(id=pid).first() is None:
        return _not_found()

    p = request.get_json(silent=True) or {}
    try:
        if method_name == "mark_ordered":
            svc.mark_ordered(pid)
        else:
            getattr(svc, method_name)(pid, user=api_user,
                                      remarks=p.get("remarks"))
    except Exception as exc:
        return _conflict(str(exc))

    pr = PurchaseRequest.query.filter_by(id=pid).first()
    return jsonify(_pr_json(pr, detail=True))


@bp.route("/purchase-requests/<int:pid>/submit", methods=["POST"])
@api_auth_required("purchaserequest.update")
def submit_purchase_request(api_user, pid):
    return _lifecycle_action(api_user, pid, "submit")


@bp.route("/purchase-requests/<int:pid>/approve", methods=["POST"])
@api_auth_required("purchaserequest.view")
def approve_purchase_request(api_user, pid):
    return _lifecycle_action(api_user, pid, "approve")


@bp.route("/purchase-requests/<int:pid>/reject", methods=["POST"])
@api_auth_required("purchaserequest.view")
def reject_purchase_request(api_user, pid):
    return _lifecycle_action(api_user, pid, "reject")


@bp.route("/purchase-requests/<int:pid>/return", methods=["POST"])
@api_auth_required("purchaserequest.view")
def return_purchase_request(api_user, pid):
    return _lifecycle_action(api_user, pid, "return_document")


@bp.route("/purchase-requests/<int:pid>/cancel", methods=["POST"])
@api_auth_required("purchaserequest.update")
def cancel_purchase_request(api_user, pid):
    return _lifecycle_action(api_user, pid, "cancel")


@bp.route("/purchase-requests/<int:pid>/resubmit", methods=["POST"])
@api_auth_required("purchaserequest.update")
def resubmit_purchase_request(api_user, pid):
    """RETURNED requests go back for approval after correction --
    resubmit is on BaseTransactionService, same as Maintenance Orders."""
    return _lifecycle_action(api_user, pid, "resubmit")


@bp.route("/purchase-requests/<int:pid>/mark-ordered", methods=["POST"])
@api_auth_required("purchaserequest.update")
def mark_purchase_request_ordered(api_user, pid):
    return _lifecycle_action(api_user, pid, "mark_ordered")


@bp.route("/purchase-requests/summary", methods=["GET"])
@api_auth_required("purchaserequest.view")
def purchase_requests_summary(api_user):
    """Stat counts and total value for the list screen's tile row.

    Status is read directly from PurchaseRequest.status, which already
    carries the full physical lifecycle (DRAFT/PENDING/APPROVED/
    ORDERED/RECEIVED/REJECTED/RETURNED/CANCELLED) -- unlike Maintenance
    Orders, this model does not need a separate approval-status count,
    because "PENDING" already means "submitted, awaiting a decision" as
    a status value in its own right.
    """
    from app.modules.transactions.purchase_request.service import (
        PurchaseRequestService)

    rows, _pagination = PurchaseRequestService().list_filtered(
        user=api_user, page=1, per_page=100000)

    def count(status):
        return sum(1 for r in rows if r.status == status)

    return jsonify({
        "total": len(rows),
        "draft": count("DRAFT"),
        "pending": count("PENDING"),
        "approved": count("APPROVED"),
        "ordered": count("ORDERED"),
        "received": count("RECEIVED"),
        "rejected": count("REJECTED"),
        "returned": count("RETURNED"),
        "cancelled": count("CANCELLED"),
        "total_value": f"{sum((r.amount or 0) for r in rows):.2f}",
    })
