"""Cross-document pending-approvals worklist.

Reported: the dashboard's "For My Action" panel sent Pending Approvals
to /approvals and Due for Maintenance to /reports/due-maintenance --
both placeholder routes with nothing behind them.

Flask's own dashboard has no separate /approvals page either --
"Pending Approvals" is `#for-my-action`, an anchor to a worklist
rendered inline on the dashboard, where each task links to its actual
document via resolve_task_url(). This endpoint is that same mechanism
exposed to a bearer-token client: ApprovalTaskService.list_for_user()
already does the eligibility, org-scope and dedup work, so it is reused
rather than reimplemented.

URL resolution mirrors task_url_resolver.py's own _ROUTE_MAP, but only
for reference_table values React has a built screen for
(maintenance_orders). Everything else resolves to None -- exactly what
task_url_resolver's own docstring says about tables it does not
recognise ("future modules simply won't be clickable until added"),
not an error and not invented.
"""
from flask import jsonify, request

from app.modules.api.auth import api_auth_required
from app.modules.api.routes import bp

#: Only tables with a real React detail screen. Extend this the same
#: way task_url_resolver.py is extended -- one entry per module as it
#: ships, never guessed at ahead of the screen existing.
_REACT_ROUTE_MAP = {
    "maintenance_orders": "/maintenance-orders/{id}",
    # Added after the client found this exact gap: the PR detail screen
    # (react-v72/73) had existed for two commits before this map was
    # updated, so every PR approval task kept showing "Not yet
    # available here" for a screen that was already built and working.
    # A route map that has to be remembered separately from the screen
    # it points at will drift; nothing enforces the two stay in sync
    # except a human noticing, which is what happened here.
    "purchase_requests": "/purchase-requests/{id}",
}


def _task_url(task):
    template = _REACT_ROUTE_MAP.get(task.reference_table)
    if template is None:
        return None
    return template.format(id=task.reference_id)


@bp.route("/worklist/pending-approvals", methods=["GET"])
@api_auth_required()
def pending_approvals(api_user):
    """PENDING tasks this user can act on, across every document type.

    Gated on auth only, not a specific permission: eligibility is
    entirely ApprovalTaskService's (direct assignment, or role + org
    scope), and a second permission check here would be a second,
    potentially drifting definition of who may see their own worklist.
    """
    from app.core.approval.task_service import ApprovalTaskService

    tasks = ApprovalTaskService().list_for_user(api_user)

    # Returned documents belong on the initiator's action list so they
    # can attach what the approver asked for and resubmit.
    returned_items = _returned_for_requester(api_user)

    # Honour the dashboard's branch selector. Reported: the approval
    # figures did not tie up with the branch chosen in the header,
    # because this endpoint ignored it entirely while every other
    # dashboard count already took branch_id. Filtered here rather than
    # inside list_for_user, which is shared with the approvals COUNT and
    # with non-dashboard callers that legitimately want every branch.
    branch_id = request.args.get("branch_id", type=int)
    if branch_id is not None:
        tasks = [t for t in tasks if t.branch_id == branch_id]
    return jsonify({"items": [
        {
            "id": t.id,
            "reference_table": t.reference_table,
            "reference_id": t.reference_id,
            "document_number": t.document_number,
            "document_type": t.document_type.name if t.document_type else None,
            "level_number": t.level_number,
            "created_at": t.created_at.isoformat() if t.created_at else None,
            # None rather than a guess: a table this map does not cover
            # yet still gets LISTED (the document number is still
            # useful information) but is not made a dead or wrong link.
            "url": _task_url(t),
            "kind": "approval",
        }
        for t in tasks
    ] + returned_items})


def _returned_for_requester(api_user):
    from app.core.approval.models import ApprovalInstance
    rows = (ApprovalInstance.query
            .filter_by(submitted_by=api_user.id, status="RETURNED")
            .order_by(ApprovalInstance.id.desc())
            .all())
    items = []
    for inst in rows:
        template = _REACT_ROUTE_MAP.get(inst.reference_table)
        items.append({
            "id": f"returned-{inst.id}",
            "reference_table": inst.reference_table,
            "reference_id": inst.reference_id,
            "document_number": None,
            "document_type": (
                inst.document_type.name if inst.document_type else None),
            "level_number": inst.current_level,
            "created_at": inst.created_at.isoformat()
                if getattr(inst, "created_at", None) else None,
            "url": (template.format(id=inst.reference_id)
                    if template else None),
            "kind": "returned",
        })
    return items
