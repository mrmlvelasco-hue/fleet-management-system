"""Whether the signed-in user may act on a document's approval, right now.

Six endpoints each wrote this as:

    data["can_act"] = bool(user and engine.is_eligible_approver(inst, user))

which asks only "are you an eligible approver on this instance". It
never asks whether the DOCUMENT is actually awaiting approval.

What it adds is the instance's own state: an approval instance that has
already been decided, or a document that has reached a terminal state,
must not still offer a decision.

NOTE on the client screenshot that prompted this: a trip ticket showing
both "Submit for Approval" and "Approve & Forward" is NOT fixed here.
That turned out to be the other side of the same screen -- submit was
being offered on a document whose approval was already pending -- and
the fix belongs in the submit gate, not the approve gate. Recorded
because the obvious reading of that screenshot is the wrong one.
"""

#: Document states in which no approval decision is possible.
#:
#: DRAFT is deliberately NOT here, though it was on the first attempt.
#: Several modules -- trip tickets among them -- leave the document in
#: DRAFT on submit and move only the approval instance to PENDING, so
#: DRAFT + PENDING is the NORMAL awaiting-approval state, not an
#: anomaly. Excluding it hid the decision from every legitimate
#: approver, and the return-and-resubmit test caught it immediately.
#:
#: What remains are the terminal states: there is nothing left to
#: decide, and an instance can outlive the document reaching one.
NOT_AWAITING_APPROVAL = {"CANCELLED", "REJECTED", "CLOSED", "COMPLETED"}


def can_act_on(instance, api_user, document_status=None) -> bool:
    """True only when this user may approve THIS document AS IT STANDS.

    Three conditions, all required:

      * an approval instance exists and is still PENDING;
      * the document is not in a state where approval is meaningless;
      * the engine considers this user an eligible approver.

    `document_status` is optional so a caller that genuinely has no
    status can still use this -- but every module endpoint should pass
    it, because it is the condition the old code was missing.
    """
    from app.core.approval.engine import ApprovalEngine

    if instance is None or api_user is None:
        return False
    if getattr(instance, "status", None) != "PENDING":
        return False
    if document_status and str(document_status).upper() in NOT_AWAITING_APPROVAL:
        return False
    return bool(ApprovalEngine().is_eligible_approver(instance, api_user))
