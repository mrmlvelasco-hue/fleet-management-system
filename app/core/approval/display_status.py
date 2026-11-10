"""The one user-facing status label, shared by every transaction
module that follows the shared BaseTransactionService recipe.

A document's physical `status` column defaults to "DRAFT" and stays
there through BOTH submission and approval -- `submit()` in
base_service.py only sets `approval_instance_id`, never `status`; each
module only moves its own `status` forward once the INITIATOR takes
that module's own closing action (completing a Vehicle Registration,
releasing a Trip Ticket, ordering a Purchase Request, mounting a
Tire/Battery, activating an ATD, starting work on a Maintenance
Order, ...). So "status == DRAFT" alone conflates three different
situations an initiator or approver actually cares about telling
apart: never submitted, sitting with an approver, and fully approved
but not yet closed out.

Extracted from maintenance_orders.py's own `_display_status` -- the
only module that had this right, because changing it would have
broken EDITABLE_STATUSES-driven edit permissions and physical-status
reports if it were done by changing the stored `status` column
instead. (2026-11-10: considered, and rejected for that reason --
see claude/dashboard-draft-queue-decisions.md in the project docs.)
This is the SAME computation generalised and reused instead, so every
other module gets it for free with zero storage or edit-permission
risk.
"""


def _humanize(value: str) -> str:
    return value.replace("_", " ").title() if value else "Draft"


def resolve_display_status(status: str | None, approval_instance) -> str:
    """The label a person should see for this document right now.

    `status` is the module's own physical status column value.
    `approval_instance` is the linked ApprovalInstance (or any object
    with a `.status` attribute), or None if never submitted.

    Once the physical status has moved past "DRAFT" -- the module's own
    closing action has run -- that status wins outright and is shown
    as-is (humanized): an approval instance left over in ANY state must
    never override a document that has already moved on. Only while
    still "DRAFT" does the approval instance decide what to show.
    """
    phys = (status or "").upper()
    if phys and phys != "DRAFT":
        return _humanize(phys)

    appr = approval_instance.status if approval_instance is not None else None
    if appr == "PENDING":
        return "Awaiting Approval"
    if appr == "RETURNED":
        return "Returned for Revision"
    if appr == "REJECTED":
        return "Rejected"
    if appr == "APPROVED":
        return "Approved"
    return "Draft"
